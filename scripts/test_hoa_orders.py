"""Fixed-order eligibility, dimensional reports and portable input failure semantics."""
import hashlib,json,struct,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary,assert_hoa_fast
import hoa_vectors as ambient
import hoa_salient_vectors as salient
from hoa_orders_vectors import state_fixtures
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class HoaOrderTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-hoa-orders-test-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.index=0
    def path(self):self.index+=1;return self.root/str(self.index)
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def test_new_dimensions_and_rate_are_reported_without_padding(self):
        for module,order,rate in ((ambient,1,44100),(salient,2,48000)):
            n=(order+1)**2;case=ambient.excitation(3,order=1) if order==1 else salient.basis(8,0,5,order=2,angles=(37,121))
            root=self.path();module.bundle(root,[module.packet(case,order=order,rate=rate)[0]],order=order,rate=rate)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,0,p.stderr)
            summary=json.loads(p.stdout);r=json.loads((root/'parsed').read_text())['report'];h=r['hoa'];self.assertEqual(summary['context']['transport']['sample_rate_hz'],rate)
            self.assertEqual((h['order'],h['coefficient_count'],h['transport_channels']),(order,n,n));self.assertEqual(len(h['channels_after_hoa']),n);self.assertEqual(len(r['elements']),n)
            if order==2:self.assertTrue(all(len(d['restored'])==9 for d in h['spatial']['salient']['descriptors']));self.assertTrue(any(h['channels_after_hoa'][8]['scaled']))
            out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((out/'pcm.f32le').stat().st_size,1024*n*4)
    def test_declared_capacity_and_new_order_failures(self):
        for module,order,cap in ((ambient,1,8192),(salient,2,18432)):
            root=self.path();module.bundle(root,[pack('10001'+bits(cap+1,16))],order=order)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,1);self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        for f in state_fixtures()['fixtures']:
            module=ambient if f['order']==1 else salient
            for key in ('last_element_error','late_spatial_error','late_tail_error','embedded_error','outer_after_embedded_error'):
                root=self.path();module.bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(f[key])],order=f['order'],rate=f['rate'],drc=True,rich=True);out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1,key);error=json.loads(p.stderr)['error'];self.assertEqual(error['packet_index'],1);self.assertIn('bit_offset',error)
                self.assertTrue((out/'.incomplete.json').exists());self.assertFalse((out/'decode-sq.json').exists())
    def test_same_count_channel_asc_is_not_hoa_and_chan_must_match(self):
        fields=[(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),(31,6),(0,4),(0,1),(3,6),(0,6),(4,8),(2,8),(0,1),(1,3),(0,8),(0,3),(0,1),(4,5)]
        wire=''.join(bits(v,w) for v,w in fields)+'000'*4+bits(108,16)+'00'+bits(0,3)+bits(0,2)+'000000'
        raw=pack(wire);config=len(raw).to_bytes(4,'big')+raw[4:];path=self.path();path.write_bytes(config)
        p=self.run_tool('parse-cookie',path);self.assertEqual(p.returncode,0,p.stderr)
        source=self.path();source.write_bytes(caf(config,[ambient.packet({},order=1)[0]],channels=4)[0]);out=self.path()
        p=self.run_tool('decode-sq',source,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('qualified HOA',p.stderr)
        raw,truth=caf(ambient.cookie(order=1),[ambient.packet({},order=1)[0]],channels=4);bad=bytearray(raw);position=truth['chunks']['chan']['offset'];bad[position:position+4]=struct.pack('>I',(190<<16)|9)
        source=self.path();source.write_bytes(bad);out=self.path();p=self.run_tool('decode-sq',source,'--out',out);self.assertEqual(p.returncode,1);self.assertEqual(json.loads(p.stderr)['error']['chunk_type'],'chan');self.assertFalse(out.exists())
    def test_expanded_orders_selection_and_rate_are_supported(self):
        from portable_tools import assert_hoa_configuration
        for change, rate in ((dict(component_orders=[1, 2, 2, 2, 2]), 48000),
                             (dict(path='replace', ambient_count=4, selection=[0, 1, 2, 3]), 48000),
                             ({}, 32000)):
            assert_hoa_configuration(self, dict(order=2, counts=[4]*5, **change), rate)
    def test_container_tail_empty_fast_and_output_protection(self):
        for module,order,rate in ((ambient,1,48000),(salient,2,44100)):
            n=(order+1)**2;case=ambient.excitation(3,order=1) if order==1 else salient.basis(8,4,1,order=2);packets=[module.packet(case,order=order,rate=rate)[0]]*3
            for encoder in (caf,mp4):
                source=self.path();source.write_bytes(encoder(module.cookie(order=order,rate=rate),packets,rate=rate,channels=n)[0]);out=self.path()
                assert_hoa_fast(self,source,out);out=self.path()
                out=self.path();p=self.run_tool('decode-sq',source,'--out',out,'--start-frame',3072,'--frames',1);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((out/'pcm.f32le').read_bytes(),b'');self.assertTrue(json.loads(p.stdout)['input']['consistency_verified'])
                out=self.path();p=self.run_tool('decode-sq',source,'--out',out,'--start-frame',3069,'--frames',100);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((out/'pcm.f32le').stat().st_size,3*n*4)
                before=(out/'pcm.f32le').read_bytes();p=self.run_tool('decode-sq',source,'--out',out);self.assertEqual(p.returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path();salient.bundle(root,[salient.packet({},order=2)[0]]*32,order=2);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1);self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').exists())


if __name__=='__main__':unittest.main()
