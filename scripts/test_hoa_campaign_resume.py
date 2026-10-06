"""Campaign setup and pilot gates survive interruptions without new captures."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from hoa_blackbox_lib import campaign
from hoa_blackbox_lib.campaign_store import create_pool
from hoa_blackbox_lib.common import BudgetStop, IdentityError, tool_fingerprint
from hoa_blackbox_lib.native import import_batch
from hoa_blackbox_lib.store import Store
from test_hoa_blackbox import FakeBackend, fake_engine, new_store, synthetic_priors


def campaign_config(root):
    root.mkdir()
    (root/'batches').mkdir()
    limits = dict(max_bytes=512*1024**2, max_calls=4096, min_free=0)
    totals = dict(max_bytes=1024**3, max_calls=50000)
    create_pool(root, totals)
    config = dict(schema_version=1, plan=campaign.make_plan([4]), code_commit='synthetic',
                  tool_fingerprint=tool_fingerprint(), native_identity=FakeBackend.identity,
                  binary='/synthetic/apac-tool', evidence_device=root.stat().st_dev,
                  limits=limits, total_limits=totals, jobs=1, status='interrupted',
                  orders={'4': dict(status='running', application='pending')})
    campaign.save(root, config)
    return config


class CampaignResumeTests(unittest.TestCase):
    def test_setup_resumes_snapshot_import_and_priors_without_recapture(self):
        for interrupted_stage in ('snapshot', 'import', 'priors'):
            with self.subTest(stage=interrupted_stage), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                source = base/'source'
                original = new_store(source/'batches/order4-q7', quantization_bits=7, order=4)
                try:
                    backend = FakeBackend(quantization_bits=7, order=4)
                    runner = fake_engine(original, backend).runner
                    values = ([q]*runner.writer.symbols for q in (32, 48))
                    payloads = [runner.writer.fixed(v) for v in values]
                    keys = [runner.probe('mode1', 'setup', str(i), payload)[0]
                            for i, payload in enumerate(payloads)]
                finally:
                    original.close()
                root = base/'campaign'
                config = campaign_config(root)
                config['evidence_campaigns'] = [str(source)]
                priors = synthetic_priors(4)
                original_save = Store.save_stage
                original_import = Store.imported_query

                def interrupted_snapshot(store):
                    store.blob(b'incomplete source snapshot')
                    raise BudgetStop('minimum free disk space reached')

                def interrupted_import(store, *args):
                    original_import(store, *args)
                    raise BudgetStop('minimum free disk space reached')

                def interrupted_priors(store, target, name, value):
                    if name == 'priors':
                        raise BudgetStop('minimum free disk space reached')
                    return original_save(store, target, name, value)

                injections = {
                    'snapshot': patch.object(campaign, 'snapshot', interrupted_snapshot),
                    'import': patch.object(Store, 'imported_query', interrupted_import),
                    'priors': patch.object(Store, 'save_stage', interrupted_priors),
                }
                with injections[interrupted_stage], self.assertRaises(BudgetStop):
                    campaign.open_batch(root, config, 4, 7, priors)
                with patch('hoa_blackbox_lib.native.import_batch', wraps=import_batch) as importer:
                    resumed = campaign.open_batch(root, config, 4, 7, priors)
                    try:
                        self.assertTrue(resumed.meta('initialization_complete'))
                        self.assertIsNotNone(resumed.meta('source_snapshot'))
                        self.assertEqual(resumed.stage('_shared', 'priors'), priors)
                        self.assertEqual(importer.call_count, int(interrupted_stage != 'priors'))
                        backend = FakeBackend(quantization_bits=7, order=4)
                        runner = fake_engine(resumed, backend).runner
                        for i, payload in enumerate(payloads):
                            self.assertEqual(runner.probe('mode1', 'setup', str(i), payload)[0], keys[i])
                        self.assertEqual(backend.calls, 0)
                        self.assertEqual(resumed.summary()['native_calls'], 0)
                    finally:
                        resumed.close()
                with patch.object(campaign, 'snapshot') as snapshot, \
                        patch('hoa_blackbox_lib.native.import_batch') as importer:
                    ready = campaign.open_batch(root, config, 4, 7, priors)
                    ready.close()
                    snapshot.assert_not_called()
                    importer.assert_not_called()

    def test_explicit_budget_update_precedes_setup_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'campaign'
            config = campaign_config(root)
            config['limits']['max_bytes'] = 1
            priors = synthetic_priors(4)
            with self.assertRaises(BudgetStop):
                campaign.open_batch(root, config, 4, 7, priors)
            config['limits']['max_bytes'] = 512*1024**2
            store = campaign.open_batch(root, config, 4, 7, priors, limits_updated=True)
            try:
                self.assertTrue(store.meta('initialization_complete'))
                self.assertEqual(store.config['limits'], config['limits'])
                self.assertEqual(store.summary()['native_calls'], 0)
                self.assertEqual(len(store.summary()['shards']), 1)
            finally:
                store.close()
            with self.assertRaises(IdentityError):
                campaign.open_batch(root, config, 4, 7, dict(priors, architecture='changed'))

    def test_resumed_fourth_order_pilot_refuses_every_failed_terminal_state(self):
        for target in ('mode1', 'mode4:0'):
            for outcome in ('failed', 'partial', 'different', 'blocked'):
                with self.subTest(target=target, outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)/'campaign'
                    config = campaign_config(root)
                    store = campaign.open_batch(root, config, 4, 6)
                    if target == 'mode4:0':
                        store.job('mode1', 'passed')
                    store.job(target, outcome)
                    store.close()
                    args = SimpleNamespace(out=root, binary=None, jobs=None)
                    backend = lambda binary, precision=6, order=4: FakeBackend(quantization_bits=precision, order=order)
                    with patch('hoa_blackbox_lib.native.NativeBackend', backend), \
                            patch('hoa_blackbox_lib.engine.Engine', autospec=True) as engine, \
                            patch.object(campaign, 'compare_batch') as compare:
                        result = campaign.run(args, lambda event: None)
                        engine.return_value.run.assert_not_called()
                        compare.assert_not_called()
                    self.assertEqual(result['status'], 'pilot_failed')
                    self.assertEqual(result['orders']['4']['status'], 'pilot_failed')
                    self.assertEqual(result['native_calls'], 0)
                    self.assertEqual(campaign.read(root)['status'], 'pilot_failed')

    def test_passed_pilots_allow_resuming_the_next_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'campaign'
            config = campaign_config(root)
            store = campaign.open_batch(root, config, 4, 6)
            for target in ('mode1', 'mode4:0'):
                store.job(target, 'passed')
            store.close()
            args = SimpleNamespace(out=root, binary=None, jobs=None)
            backend = lambda binary, precision=6, order=4: FakeBackend(quantization_bits=precision, order=order)
            with patch('hoa_blackbox_lib.native.NativeBackend', backend), \
                    patch('hoa_blackbox_lib.engine.Engine', autospec=True) as engine:
                engine.return_value.run.side_effect = BudgetStop('stop at the next target')
                with self.assertRaisesRegex(BudgetStop, 'next target'):
                    campaign.run(args, lambda event: None)
                engine.return_value.run.assert_called_once_with(targets=['mode2:0', 'mode2:1'])


if __name__ == '__main__':
    unittest.main()
