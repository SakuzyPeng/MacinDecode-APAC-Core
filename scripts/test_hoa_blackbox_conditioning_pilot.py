"""Controlled pilot must freeze a passing receipt before batch expansion."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hoa_blackbox_lib.common import EvidenceError, ExperimentError, canonical, digest
from hoa_blackbox_lib.campaign import check_conditioning_pilot, finish_conditioning_pilot


class PilotStore:
    def __init__(self, root):
        self.config = dict(order=9, quantization_bits=6)
        path = root/'batches/order9-q6'
        path.mkdir(parents=True)
        self.db = sqlite3.connect(path/'state.sqlite3')
        self.db.execute('CREATE TABLE stages(target TEXT,name TEXT,sha TEXT)')
        book = dict(order=9, quantization_bits=6, mode=4, book=0,
                    entries=[dict(symbol=q,codeword=f'{q:06b}',bit_length=6) for q in range(64)])
        matrix = dict(order=9,rows=100,columns=100,
                      entries=[dict(row=i//100,column=i%100,empirical_half_width=5e-8,
                                    candidate_bits=[0x3f800000],float32_bits=0x3f800000) for i in range(10000)])
        boot = dict(condition_inf=70., inverse_residual_inf=1e-14,
                    inversion_policy=dict(policy='order9-10-condition128-gram-v1',order=9,
                        condition_inf_limit=128,normalized_gram_residual_inf_limit=1e-3,
                        inverse_residual_inf_limit=1e-10,normalized_gram_residual_inf=5e-5))
        self.stages = dict(bootstrap=boot,codebook=book,matrix=matrix,comparison=dict(status='passed'))
        self.bind()

    def bind(self):
        self.stages['validation'] = dict(status='passed',checks=[dict(label='synthetic')],
            codebook_sha256=digest(canonical(self.stages['codebook'])),
            matrix_sha256=digest(canonical(self.stages['matrix'])),
            matrix_qualified=sum(e['float32_bits'] is not None for e in self.stages['matrix']['entries']))
        with self.db:
            self.db.execute('DELETE FROM stages')
            self.db.executemany('INSERT INTO stages VALUES (?,?,?)',
                               [('mode4:0',name,digest(canonical(value))) for name,value in self.stages.items()])

    def stage(self, target, name):
        assert target=='mode4:0'
        return copy.deepcopy(self.stages.get(name))


def config():
    return dict(tool_fingerprint='synthetic',native_identity=dict(synthetic=True),
                orders={'9':{'status':'running'}},
                conditioning_pilot=dict(order=9,precision=6,target='mode4:0',status='pending'))


class PilotTests(unittest.TestCase):
    def test_success_receipt_binds_stages_and_detects_changed_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=PilotStore(root);state=config()
            try:
                finish_conditioning_pilot(root,state,store,'passed')
                self.assertEqual(state['status'],'pilot_complete')
                check_conditioning_pilot(root,state)
                receipt=root/'conditioning-pilot.json'
                receipt.write_bytes(receipt.read_bytes()+b' ')
                with self.assertRaisesRegex(EvidenceError,'receipt changed'):
                    check_conditioning_pilot(root,state)
            finally:store.db.close()

    def test_zero_only_partial_can_expand_without_qualifying_zero_bits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=PilotStore(root);state=config()
            try:
                store.stages['matrix']['entries'][13].update(float32_bits=None,candidate_bits=[0,0x80000000])
                store.bind()
                finish_conditioning_pilot(root,state,store,'partial')
                self.assertEqual(state['status'],'pilot_complete')
                value=json.loads((root/'conditioning-pilot.json').read_text())
                self.assertEqual(value['matrix_qualified'],9999)
                self.assertEqual(value['unresolved_zero_indices'],[[0,13]])
                self.assertFalse(value['original_zero_bits_assigned'])
                self.assertIsNone(store.stages['matrix']['entries'][13]['float32_bits'])
                with store.db:
                    store.db.execute("UPDATE stages SET sha='changed' WHERE name='comparison'")
                with self.assertRaisesRegex(EvidenceError,'frozen stages changed'):
                    check_conditioning_pilot(root,state)
            finally:store.db.close()

    def test_precision_and_nonzero_ambiguity_stop_pilot(self):
        for change in (dict(empirical_half_width=2.5e-7),
                       dict(float32_bits=None,candidate_bits=[0x3f800000,0x3f800001])):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);store=PilotStore(root);state=config()
                try:
                    store.stages['matrix']['entries'][0].update(change);store.bind()
                    finish_conditioning_pilot(root,state,store,'partial')
                    self.assertEqual(state['status'],'pilot_failed')
                    self.assertFalse((root/'conditioning-pilot.json').exists())
                    with self.assertRaisesRegex(ExperimentError,'did not pass'):
                        check_conditioning_pilot(root,state)
                finally:store.db.close()

    def test_different_reference_result_never_allows_expansion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);store=PilotStore(root);state=config()
            try:
                finish_conditioning_pilot(root,state,store,'different')
                self.assertEqual(state['status'],'pilot_failed')
            finally:store.db.close()


if __name__=='__main__':unittest.main()
