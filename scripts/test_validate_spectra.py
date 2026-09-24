"""Portable spectral acceptance gates and CLI failure semantics."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from spectrum_vectors import bundle, frame, matrix_cases, band_cases
from validate_spectra import check_expected, check_native


class SpectrumTests(unittest.TestCase):
    def setUp(self):
        self.binary = Path(__file__).resolve().parents[1] / 'target/debug/apac-tool'
        if not self.binary.exists():
            self.skipTest('build apac-tool before CLI acceptance tests')
        self.tmp = tempfile.TemporaryDirectory(prefix='apac-spectra-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def parse(self, payloads, *args):
        bundle(self.root/'packets', payloads)
        return subprocess.run([str(self.binary), 'parse-packets', str(self.root/'packets'),
            '--depth', 'spectrum', '--output', str(self.root/'spectra.jsonl'), *args],
            capture_output=True, text=True, timeout=30)

    def metrics(self):
        return dict(max_absolute_error=0.0, max_ulp=0, coefficients=0)

    def test_integer_float_and_native_evidence_cannot_be_faked(self):
        data, expected = frame(dict(block=2, grouping=0x55,
            left={0:(11,[8191,-17],155),3:(1,[1,-1,0,1],170)}, right={13:(3,[1,-1,0,1],150)}))
        result = self.parse([data])
        self.assertEqual(result.returncode, 2, result.stderr)
        row = json.loads((self.root/'spectra.jsonl').read_text())
        report = row['report']
        check_expected(report, expected, self.metrics())
        for key, value in [('spectrum_complete',False),('status','complete'),('stop_bit_offset',0),('payload_bit_offset',0)]:
            bad = copy.deepcopy(report)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check_expected(bad,expected,self.metrics())
        for key in ['quantized','scaled']:
            bad = copy.deepcopy(report)
            bad['channels'][0][key][0] = 1
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check_expected(bad,expected,self.metrics())
        events = [dict(sequence=0,packet_sha256=report['packet_sha256'],**{k:c[k] for k in
            ['channel_index','stream_bit_offset','end_bit_offset','scaled']}) for c in report['channels']]
        trace = dict(errors=[],process_exit_code=0,packet_calls=1,spectra=events,
            events=[dict(sequence=0,packet_sha256=report["packet_sha256"],boundary="sq_left_channel_stream",native_bit_offset=report["payload_bit_offset"])])
        self.assertEqual(check_native([row],trace,self.metrics()),2)
        for key,value in [('end_bit_offset',0),('packet_sha256','bad'),('scaled',[0.]*1024)]:
            bad=copy.deepcopy(trace)
            bad['spectra'][0][key]=value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check_native([row],bad,self.metrics())

    def test_shared_ics_and_absence_do_not_count_as_two_spectra(self):
        data,expected=frame(dict(left={0:(1,[1,0,0,0],160)}))
        shared=bytearray(data)
        bit=expected[0]['end_bit_offset']
        shared[bit//8] |= 1 << (7-bit%8)
        result=self.parse([bytes(shared),b'\x40',b'\x70'])
        self.assertEqual(result.returncode,2,result.stderr)
        summary=json.loads(result.stdout)
        self.assertEqual((summary['left_spectrum_packets'],summary['right_spectrum_packets'],summary['spectrum_complete_packets'],summary['cpe_absent_packets']),(1,0,0,1))
        rows=[json.loads(s) for s in (self.root/'spectra.jsonl').read_text().splitlines()]
        self.assertEqual(rows[0]['report']['stop_reason'],'shared_ics_cac_deferred')
        self.assertFalse((self.root/'spectra.jsonl.incomplete').exists())

    def test_syntax_error_continues_and_preserves_marker(self):
        data,_=frame({})
        result=self.parse([data,b'\x60\x10',data])
        self.assertEqual(result.returncode,1,result.stderr)
        summary=json.loads(result.stdout)
        self.assertEqual((summary['actual_packets'],summary['errors'],summary['spectrum_complete_packets']),(3,1,2))
        rows=[json.loads(s) for s in (self.root/'spectra.jsonl').read_text().splitlines()]
        self.assertEqual(rows[1]['packet_index'],1)
        self.assertEqual(rows[1]['error']['bit_offset'],12)
        self.assertTrue((self.root/'spectra.jsonl.incomplete').exists())

    def test_spectrum_quota_and_existing_report_are_not_overwritten(self):
        data,_=frame({})
        result=self.parse([data]*200,'--packets','200','--max-output-mib','1')
        self.assertEqual(result.returncode,1)
        output=self.root/'spectra.jsonl'
        self.assertTrue(output.exists())
        self.assertTrue((self.root/'spectra.jsonl.incomplete').exists())
        original=output.read_bytes()
        result=subprocess.run([str(self.binary),'parse-packets',str(self.root/'packets'),'--depth','spectrum','--output',str(output)],capture_output=True,timeout=30)
        self.assertEqual(result.returncode,1)
        self.assertEqual(output.read_bytes(),original)

    def test_matrices_keep_all_tuple_gain_escape_and_band_controls(self):
        matrix = list(matrix_cases())
        self.assertEqual(len(matrix),4730)
        escapes = [c['left'][0][1] for c in matrix if c['kind']=='escape_boundary']
        self.assertEqual(len(escapes),110)
        self.assertEqual({max(map(abs,pair)) for pair in escapes}, {15,16,17,31,32,33,63,64,127,128,255,256,511,512,1023,1024,2047,2048,4095,4096,8190,8191})
        cases=list(band_cases())
        self.assertTrue(all(any(c.get('block')==block for c in cases) for block in range(4)))
        self.assertEqual({next(iter(c['left'])) for c in cases if c['kind']=='band' and c['block']==2 and 'left' in c},set(range(14)))


if __name__=='__main__':
    unittest.main()
