"""Source-layout API, refusal boundaries and container consistency."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
import hoa_source_layout_vectors as vectors
from caf_vectors import encode


class SourceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary=Path(os.environ.get('APAC_TOOL_BINARY',vectors.ROOT/'target/debug/apac-tool')).resolve()

    def command(self,*args,code=0):
        r=subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True)
        self.assertEqual(r.returncode,code,r.stdout+r.stderr)
        return json.loads(r.stdout if r.stdout.strip() else r.stderr.splitlines()[-1])

    def test_explicit_n3d_metadata_preserves_label_indices(self):
        opts=vectors.options(4,labels=[196611,196608,196610,196609],parameter=2)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,_=vectors.packet(vectors.basis(opts,3),**opts)
            vectors.bundle(root/'bundle',[raw],**opts)
            result=self.command('decode-sq',root/'bundle','--out',root/'pcm')
            self.assertEqual(result['pcm']['decoder_settings']['implementation']['value']['hoa_source_normalization'],'N3D')
            self.assertEqual([d['label'] for d in result['channel_layout']['descriptions']],opts['source_layout']['labels'])
            self.assertIsNone(result['channel_layout']['ambisonic_order'])

    def test_tagged_n3d_is_parseable_but_reference_profile_rejects_it(self):
        opts=vectors.options(4,(191<<16)|4,parameter=1)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,_=vectors.packet(vectors.basis(opts),**opts)
            vectors.bundle(root/'bundle',[raw],**opts)
            parsed=self.command('parse-cookie',root/'bundle/cookie.bin')
            self.assertEqual(parsed['status'],'complete')
            result=self.command('decode-sq',root/'bundle','--out',root/'pcm',code=1)
            self.assertIn('profile layout table',result['error']['message'])
            self.assertFalse((root/'pcm').exists())

    def test_source_matrix_cannot_read_beyond_its_table(self):
        opts=vectors.options(9,(101<<16)|2,parameter=0)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,_=vectors.packet(vectors.basis(opts),**opts)
            vectors.bundle(root/'bundle',[raw],**opts)
            result=self.command('decode-sq',root/'bundle','--out',root/'pcm',code=1)
            self.assertIn('bounded coefficient table',result['error']['message'])
            self.assertFalse((root/'pcm').exists())

    def test_caf_source_labels_are_checked_against_the_cookie(self):
        opts=vectors.options(4,labels=[131072,131073,131074,131075],parameter=1)
        raw,_=vectors.packet(vectors.basis(opts),**opts)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input.caf'
            source.write_bytes(encode(vectors.cookie(**opts),[raw],channels=4,layout_tag=0,channel_labels=opts['source_layout']['labels'])[0])
            self.command('decode-sq',source,'--out',root/'good')
            source.write_bytes(encode(vectors.cookie(**opts),[raw],channels=4,layout_tag=0,channel_labels=[131075,131073,131074,131072])[0])
            result=self.command('decode-sq',source,'--out',root/'bad',code=1)
            self.assertIn('descriptions disagree with cookie',result['error']['message'])
            self.assertFalse((root/'bad').exists())


if __name__=='__main__':unittest.main()
