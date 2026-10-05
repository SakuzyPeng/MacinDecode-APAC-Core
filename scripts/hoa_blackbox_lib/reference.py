"""Final reference comparison. Imported only by the separate compare command."""
import json
from .common import ROOT, EvidenceError, canonical, digest, file_digest, require, target_parts
from .maths import validate_words


def compare(store, targets):
    from hoa_packed_tables import unpack_codebook, unpack_matrix
    precision = store.config.get('quantization_bits', 6)
    dictionary_path = ROOT / 'data' / ('hoa-salient-format-v1.json' if precision == 6 else f'hoa-salient-order3-q{precision}-format-v1.json')
    shared_path = ROOT / 'data/hoa-salient-order3-shared-v1.json'
    for target in targets:
        book = store.stage(target, 'codebook')
        validation = store.stage(target, 'validation')
        if book is None or validation is None:
            continue
        require(validation['status'] == 'passed' and validation['codebook_sha256'] == digest(canonical(book)),
                'candidate is not frozen and validated', EvidenceError)
        require(book.get('quantization_bits', 6) == precision, 'candidate quantization width differs', EvidenceError)
        validate_words(book['entries'], 1 << precision)
        mode, cluster = target_parts(target)
        matrix = store.stage(target, 'matrix') if mode == 4 else None
        if matrix:
            require(validation['matrix_sha256'] == digest(canonical(matrix)), 'matrix validation binding differs', EvidenceError)
        if mode in (2, 3):
            layout = store.stage('_mode2' if mode == 2 else target, 'layout')
            require(layout is not None and validation['layout_sha256'] == book['layout_sha256'] == digest(canonical(layout))
                    and book['coefficient_group'] == layout['groups'][cluster or 0],
                    'coefficient partition is not frozen and validated', EvidenceError)
        if book.get('prior_sha256') is not None:
            priors = store.stage('_shared', 'priors')
            require(priors is not None and book['prior_sha256'] == validation['prior_sha256']
                    == store.config['prior_sha256'] == digest(canonical(priors)), 'prior validation binding differs', EvidenceError)
        previous = store.stage(target, 'comparison')
        if previous is not None:
            require(previous['codebook_sha256'] == digest(canonical(book))
                    and previous['validation_sha256'] == digest(canonical(validation))
                    and (matrix is None or previous['matrix_sha256'] == digest(canonical(matrix))),
                    'frozen comparison binding differs', EvidenceError)
            store.job(target, previous['status'], comparison=previous['status'])
            continue
        # Reference dictionaries are opened only after checking the selected
        # candidate and validation bindings. No reference words are returned to
        # discovery or included in the job status.
        old = json.loads(dictionary_path.read_text())
        original = unpack_codebook(old['modes'][mode]['codebooks'][cluster or 0], precision)
        differences = [e['symbol'] for e, (length, code) in zip(book['entries'], original)
                       if (e['bit_length'], int(e['codeword'], 2)) != (length, code)]
        report = dict(codebook_sha256=digest(canonical(book)), validation_sha256=digest(canonical(validation)),
                      codebook_exact=not differences, differing_symbols=differences,
                      reference_dictionary_sha256=file_digest(dictionary_path), matrix_exact=None,
                      eligible_codebook=not differences, eligible_matrix=False)
        if book.get('prior_sha256') is not None:
            report.update(prior_sha256=book['prior_sha256'], matrix_reused=book.get('matrix_reused', False),
                          group_reused=book.get('group_reused', False))
        if mode in (2, 3):
            shared = json.loads(shared_path.read_text())
            reference_group = shared['groups'][shared['modes'][mode]['group_indices'][cluster or 0]]
            exact = book['coefficient_group'] == reference_group
            report.update(group_exact=exact, eligible_codebook=not differences and exact,
                          layout_sha256=book['layout_sha256'], reference_shared_sha256=file_digest(shared_path))
        if matrix:
            shared = json.loads(shared_path.read_text())
            words = unpack_matrix(shared['matrices_f32'][shared['modes'][4]['matrix_indices'][cluster]], 256)
            wrong = [i for i, (e, word) in enumerate(zip(matrix['entries'], words))
                     if e['float32_bits'] is not None and e['float32_bits'] != word]
            unqualified = sum(e['float32_bits'] is None for e in matrix['entries'])
            exact = not wrong and unqualified == 0
            report.update(matrix_sha256=digest(canonical(matrix)), matrix_exact=exact,
                          matrix_unqualified=unqualified, differing_matrix_indices=wrong,
                          reference_shared_sha256=file_digest(shared_path), eligible_matrix=exact and not differences)
        status = 'passed' if report['eligible_codebook'] and (matrix is None or report['eligible_matrix']) else 'partial'
        if differences or report.get('differing_matrix_indices') or report.get('group_exact') is False:
            status = 'different'
        report['status'] = status
        store.save_stage(target, 'comparison', report)
        store.job(target, status, comparison=status)
