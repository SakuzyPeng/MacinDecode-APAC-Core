"""Coordinate-only recovery keeps original zero bit patterns unresolved."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from hoa_blackbox_lib.common import canonical,digest,EvidenceError
from hoa_blackbox_lib.campaign_store import create_pool
from hoa_blackbox_lib.engine import Engine
from hoa_blackbox_lib.native import Runner,import_batch
from hoa_blackbox_lib.maths import inverse
from hoa_blackbox_lib import wire
from test_hoa_blackbox import FakeWriter
from test_hoa_blackbox_campaign import pooled,runner,HighFakeBackend
from hoa_blackbox_coordinate import CoordinateStore,CoordinateEngine,POLICY,fingerprint,export_coordinates,status


def new_coordinate(root,priors,q=7,name=None):
    if not (root/'evidence.sqlite3').exists():
        (root/'batches').mkdir();create_pool(root)
    config=dict(schema_version=1,coordinate_supplement=True,order=4,quantization_bits=q,
        targets=['mode4:0'],native_identity=HighFakeBackend.identity,tool_fingerprint=fingerprint(),
        campaign_root=str(root),shard_probes=128,limits=dict(max_calls=4096,max_bytes=512*1024**2,min_free=0),
        prior_sha256=digest(canonical(priors)))
    s=CoordinateStore.create(root/'batches'/(name or f'order4-q{q}'),config)
    s.save_stage('_shared','priors',priors);return s


def synthetic_prior(backend):
    rows=[[v*(-3/32) for v in row] for row in backend.matrices[0]]
    inv,condition,residual=inverse(rows)
    return dict(profile=POLICY,order=4,native_identity=backend.identity,huffman_words_included=False,
        component_sha256=backend.identity['component_sha256'],architecture=backend.identity['architecture'],
        transforms={'0':dict(scaled_matrix=rows,inverse=inv,condition_inf=condition,inverse_residual_inf=residual,
          source_signed_coordinate_scale=-3,source_quantization_bits=6,zero_bits_assigned=False,
          original_matrix_bit_patterns_exported=False,unresolved_zero_indices=[[7,3]])})


class CoordinateTests(unittest.TestCase):
    def test_previous_profiles_remain_readable_without_rewriting(self):
        for version in (1,2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                value=dict(profile=f'hoa-blackbox-proportional-coordinate-supplement-v{version}',
                           status='complete',tasks={},native_identity={'synthetic':True},
                           tool_fingerprint='old-frozen-producer',limits={},total_limits={})
                raw=canonical(value);(root/'coordinate.json').write_bytes(raw)
                result=status(root)
                self.assertEqual(result['status'],'complete')
                self.assertEqual(result['tool_fingerprint'],'old-frozen-producer')
                self.assertEqual((root/'coordinate.json').read_bytes(),raw)

    def test_synthetic_codebook_recovers_without_original_zero_bit_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);backend=HighFakeBackend(quantization_bits=7)
            backend.matrices[0][7][3]=-0.0
            prior=synthetic_prior(backend);s=new_coordinate(root,prior)
            r=Runner(s,backend,jobs=4);r.writer=FakeWriter(7,4)
            engine=CoordinateEngine(s,r);engine.run_coordinates()
            book=s.stage('mode4:0','codebook');validation=s.stage('mode4:0','validation')
            self.assertEqual([e['codeword'] for e in book['entries']],backend.words[4,0])
            self.assertFalse(book['zero_bits_assigned']);self.assertFalse(book['original_matrix_bit_patterns_used'])
            self.assertEqual(book['signed_coordinate_scale'],-6)
            self.assertEqual(validation['status'],'passed')
            self.assertIsNone(s.stage('mode4:0','matrix'))
            calls=backend.calls;engine.run_coordinates();self.assertEqual(backend.calls,calls)
            s.close()

    def test_prior_with_assigned_matrix_bits_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend=HighFakeBackend(quantization_bits=7);prior=synthetic_prior(backend)
            prior['transforms']['0']['zero_bits_assigned']=True
            s=new_coordinate(Path(tmp),prior)
            try:
                with self.assertRaisesRegex(EvidenceError,'matrix bits'):
                    CoordinateEngine(s,Runner(s,backend))
            finally:s.close()

    def test_imported_artifacts_remain_readonly_shared_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';old.mkdir();new=root/'new';new.mkdir()
            source=pooled(old);r=runner(source)
            r.probe('mode1','test','old',r.writer.fixed(wire.vector(40,order=4)))
            path=source.out;source.close()
            backend=HighFakeBackend();prior=synthetic_prior(backend)
            a=new_coordinate(new,prior,q=6,name='a');r=Runner(a,backend);r.writer=FakeWriter(6,4)
            try:
                self.assertEqual(import_batch(a,path),1)
                key,_=r.probe('mode1','test','new',r.writer.fixed(wire.vector(41,order=4)))
                cookie=a.query(key)['artifacts']['input/cookie.bin']
                self.assertFalse((new/'objects'/(cookie+'.gz')).exists())
            finally:a.close()
            b=new_coordinate(new,prior,q=6,name='b');backend=HighFakeBackend();r=Runner(b,backend);r.writer=FakeWriter(6,4)
            try:
                self.assertEqual(r.probe('mode1','test','new',r.writer.fixed(wire.vector(41,order=4)))[0],key)
                self.assertEqual(backend.calls,0)
            finally:b.close()


if __name__=='__main__':unittest.main()
