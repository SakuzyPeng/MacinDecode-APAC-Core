"""Portable SQ PCM CLI contracts, state duration and failure markers."""
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from portable_tools import required_binary

from spectrum_vectors import frame, bundle


class SynthesisCliTests(unittest.TestCase):
    def setUp(self):
        self.binary = required_binary()
        self.tmp=tempfile.TemporaryDirectory(prefix='sq-pcm-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def run_decoder(self, output='pcm', *extra):
        return subprocess.run([str(self.binary),'decode-sq',str(self.root/'packets'),'--out',str(self.root/output),*extra],capture_output=True,text=True,timeout=30)

    def test_duration_channels_fingerprint_and_no_source_access(self):
        active,_=frame(dict(left={0:(1,[1,0,0,0],160)}))
        zero,_=frame({})
        bundle(self.root/'packets',[zero,active,zero,zero])
        result=self.run_decoder()
        self.assertEqual(result.returncode,0,result.stderr)
        summary=json.loads(result.stdout)
        self.assertFalse(summary['native_apis_used'])
        self.assertTrue(summary['experimental'])
        meta=json.loads((self.root/'pcm/pcm.json').read_text())
        self.assertEqual(meta['frames'],4096)
        self.assertIsNone(meta['source'])
        self.assertTrue(meta['decoder_settings']['implementation']['value']['experimental'])
        data=(self.root/'pcm/pcm.f32le').read_bytes()
        samples=struct.unpack('<8192f',data)
        self.assertTrue(any(samples))
        self.assertTrue(all(v==0 for v in samples[1::2]))
        self.assertFalse((self.root/'pcm/.incomplete.json').exists())
        self.assertEqual(self.run_decoder().returncode,1)
        self.assertEqual((self.root/'pcm/pcm.f32le').read_bytes(),data)

    def test_container_priming_and_remainder_trim_exact_samples(self):
        active,_=frame(dict(left={0:(1,[1,0,0,0],160)}))
        bundle(self.root/'packets',[active]*4)
        self.assertEqual(self.run_decoder('raw').returncode,0)
        raw=(self.root/'raw/pcm.f32le').read_bytes()
        path=self.root/'packets/manifest.json'
        manifest=json.loads(path.read_text())
        manifest['file']['packet_table']['value']=dict(priming_frames=1024,valid_frames=1025,remainder_frames=2047)
        path.write_text(json.dumps(manifest))
        result=self.run_decoder('trimmed')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'trimmed/pcm.f32le').read_bytes(),raw[1024*8:2049*8])
        self.assertEqual(json.loads((self.root/'trimmed/pcm.json').read_text())['frames'],1025)

    def test_unsupported_packet_fails_without_a_complete_artifact(self):
        zero,_=frame({})
        bundle(self.root/'packets',[zero,b'\x70',zero])
        result=self.run_decoder()
        self.assertEqual(result.returncode,1)
        error=json.loads(result.stderr)['error']
        self.assertEqual(error['packet_index'],1)
        self.assertTrue((self.root/'pcm/.incomplete.json').exists())
        self.assertFalse((self.root/'pcm/pcm.json').exists())

    def test_output_quota_preserves_incomplete_marker(self):
        zero,_=frame({})
        bundle(self.root/'packets',[zero]*200)
        result=self.run_decoder('quota','--max-output-mib','1')
        self.assertEqual(result.returncode,1)
        self.assertTrue((self.root/'quota/.incomplete.json').exists())
        self.assertFalse((self.root/'quota/pcm.json').exists())


if __name__=='__main__':unittest.main()
