"""Bounded native concurrency; the calling thread owns every evidence write."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager

from .common import BudgetStop, pcm_samples, require


@contextmanager
def capture_batch(runner, requests):
    require(not runner.batch_active, 'nested native batch')
    if runner.jobs == 1:
        with closing((runner.probe(*request) for request in requests)) as results:
            yield results
        return
    runner.batch_active = True
    try:
        with closing(_parallel(runner, iter(requests))) as results:
            yield results
    finally:
        runner.batch_active = False


def _parallel(runner, requests):
    # Workers may write only their own raw attempt directory. Cache lookup,
    # attempt allocation, compression, deduplication and SQLite stay here.
    store = runner.store
    pending = {}
    with ThreadPoolExecutor(max_workers=runner.jobs, thread_name_prefix='hoa-native') as pool:
        try:
            exhausted = False
            while not exhausted:
                ordered, results, deferred_stop = [], {}, None
                for _ in range(runner.jobs):
                    try:
                        request = next(requests)
                    except StopIteration:
                        exhausted = True
                        break
                    key, description, packets = runner.prepare(*request)
                    cached = store.query(key)
                    if cached:
                        results[key] = key, pcm_samples(store.read_blob(cached['pcm_sha256']))
                    elif key not in pending:
                        try:
                            attempt, folder = store.begin(key, description)
                        except BudgetStop as error:
                            # Finish already reserved work before reporting the
                            # cap; its successful observations remain reusable.
                            deferred_stop = error
                            break
                        try:
                            future = pool.submit(runner.backend.capture, packets, folder)
                        except BaseException as error:
                            store.fail(attempt, str(error), interrupted=True)
                            raise
                        pending[key] = attempt, future, 1024*len(packets)
                    ordered.append(key)
                for key in ordered:
                    if key not in results:
                        attempt, future, frames = pending[key]
                        try:
                            artifacts = future.result()
                            results[key] = runner.accept(attempt, key, artifacts, frames)
                        except BaseException as error:
                            store.fail(attempt, str(error), interrupted=isinstance(error, (KeyboardInterrupt, SystemExit)))
                            if future.done():
                                pending.pop(key, None)
                            raise
                        pending.pop(key, None)
                    # Yield in request order, independent of completion order.
                    yield results[key]
                if deferred_stop is not None:
                    raise deferred_stop
        finally:
            # An interrupted/failed analysis must not leave a decoder writing
            # after the writer lock is released. Unaccepted outputs stay raw.
            if pending:
                for _, future, _ in pending.values():
                    future.cancel()
                cancel = getattr(runner.backend, 'cancel', None)
                if cancel is not None:
                    cancel()
                for attempt, future, _ in pending.values():
                    try:
                        future.result()
                    except BaseException:
                        pass
                    store.fail(attempt, 'batch ended before output acceptance', interrupted=True)
                pending.clear()
