"""Finite links, ignored tails, and physical carrier metadata."""
import itertools,json,os,subprocess,tempfile,unittest
from pathlib import Path
import hoa_remapping_vectors as vectors


class RemappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.binary=Path(os.environ.get('APAC_TOOL_BINARY',vectors.ROOT/'target/debug/apac-tool')).resolve()
    def command(self,*args,code=0):
        r=subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,code,r.stdout+r.stderr)
        return json.loads(r.stdout if r.stdout.strip() else r.stderr.splitlines()[-1])

    def test_all_three_slot_graphs_match_independent_finite_state_powers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cookie'
            for links in itertools.product(range(3),repeat=3):
                opts=vectors.options(3,3,remapping=list(links));path.write_bytes(vectors.cookie(**opts))
                try:expected=vectors.effective(links)
                except ValueError:
                    r=self.command('parse-cookie',path,code=1);self.assertIn('hoa-remapping-cycle',r['error']['message'])
                else:
                    r=self.command('parse-cookie',path);self.assertEqual(r['status'],'complete');self.assertEqual(r['derived']['components[0].hoa.remapping_core_to_transport'],expected)

    def test_ignored_tail_values_do_not_change_pcm(self):
        outputs=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for index,tail in enumerate(([0,0,0],[7,7,7])):
                opts=vectors.options(5,2,remapping=[1,0],remapping_tail=tail);raw,_=vectors.packet(vectors.marked(opts),**opts)
                vectors.bundle(root/str(index),[raw],**opts);out=root/('pcm'+str(index));r=self.command('decode-sq',root/str(index),'--out',out)
                self.assertEqual(r['pcm']['decoder_settings']['implementation']['value']['hoa_static_remapping']['ignored_tail'],tail)
                outputs.append((out/'pcm.f32le').read_bytes())
            self.assertEqual(outputs[0],outputs[1])

    def test_invalid_graphs_and_indices_fail_before_output_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for index,links in enumerate(([1,1],[2,0])):
                opts=vectors.options(5,2,remapping=links);valid=dict(opts,remapping=[1,0]);raw,_=vectors.packet(vectors.marked(valid),**valid)
                vectors.bundle(root/str(index),[raw],**opts)
                out=root/('pcm'+str(index));r=self.command('decode-sq',root/str(index),'--out',out,code=1)
                self.assertIn('hoa-remapping',r['error']['message']);self.assertFalse(out.exists())

    def test_frame_updates_keep_cookie_mapping_and_report_physical_unused_carriers(self):
        name,opts,cases=next(s for s in vectors.sequences() if s[0]=='fixed-prefix-growing-core')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);vectors.bundle(root/'bundle',[vectors.packet(c,**opts)[0] for c in cases],**opts)
            self.command('parse-packets',root/'bundle','--depth','hoa','--output',root/'parsed')
            h=json.loads((root/'parsed').read_text().splitlines()[3])['report']['hoa']
            self.assertEqual(h['core_channels'],3);self.assertEqual(len(h['static_remapping']['core_to_transport']),6)
            self.assertEqual(h['mixed']['ambient_transport_channels'],[5,0]);self.assertEqual(h['mixed']['salient_transport_channels'],[1])
            self.assertEqual(h['mixed']['unused_transport_channels'],[2,3,4,*range(6,16)])


if __name__=='__main__':unittest.main()
