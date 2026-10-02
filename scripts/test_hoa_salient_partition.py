"""Spatial/dynamic method independence and affected failure/output contracts."""
from portable_tools import assert_hoa_fast
import json,unittest
from hoa_salient_partition_vectors import cookie,packet,bundle,basis,native_controls,state_fixtures,shape
from generate_hoa_dynamic_subbands_format import boundaries
from generate_hoa_salient_partition_format import PROFILE,generate
import test_hoa_salient_subbands as legacy
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class PartitionTests(unittest.TestCase):
    setUp=legacy.SpatialSubbandTests.setUp
    path=legacy.SpatialSubbandTests.path
    run_tool=legacy.SpatialSubbandTests.run_tool
    change_cookie=legacy.SpatialSubbandTests.change_cookie

    def parsed(self,opts,cases):
        root=self.path();bundle(root,[packet(c,**opts)[0] for c in cases],**opts)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed')
        self.assertEqual(p.returncode,0,p.stderr)
        return root,[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]

    def test_all_qualified_shapes_rates_and_report_grids(self):
        shapes=[(o,p,False) for o in (2,3) for p in ('salient','replace','add')]+[(2,p,True) for p in ('salient','replace','add')]
        for order,path,dynamic in shapes:
            for rate in (44100,48000):
                for method in (1,2):
                    opts=dict(order=order,path=path,dynamic=dynamic,rate=rate,spatial_method=method,counts=[1,3,4,9,16])
                    _,rows=self.parsed(opts,[{}]);r=rows[0];side=r['hoa']['spatial']['salient']
                    self.assertEqual(r['channel_count'],shape(order,dynamic));self.assertEqual(side['partition_method'],method)
                    self.assertEqual(side['partition_profile'],PROFILE);self.assertEqual(side['format_sha256'],generate()['format_sha256'])
                    self.assertNotIn('subband_ends',side);self.assertEqual(len(side['descriptors']),33)
                    for g,n in zip(side['component_subbands'],opts['counts']):self.assertEqual(g['subband_ends'],boundaries(n,method))

    def test_uniform_four_metadata_and_old_json(self):
        for method in (0,1,2):
            opts=dict(order=3,counts=[4]*5,spatial_method=method);root,rows=self.parsed(opts,[{}]);side=rows[0]['hoa']['spatial']['salient']
            self.assertEqual(side['subband_ends'],boundaries(4,method));self.assertNotIn('component_subbands',side)
            p=self.run_tool('decode-sq',root,'--out',root/'pcm');self.assertEqual(p.returncode,0,p.stderr);impl=json.loads(p.stdout)['pcm']['decoder_settings']['implementation']['value']
            self.assertEqual((root/'pcm/pcm.f32le').read_bytes(),bytes(16*1024*4))
            if method:
                self.assertEqual(impl['hoa_salient_partition_method'],method);self.assertEqual(impl['hoa_salient_partition_profile'],PROFILE)
                self.assertEqual(impl['hoa_salient_subband_counts'],[4]*5);self.assertEqual(impl['hoa_salient_subband_format_sha256'],generate()['format_sha256'])
            else:
                for k in ('partition_method','partition_profile','format_sha256'):self.assertNotIn(k,side)
                for k in ('hoa_salient_partition_method','hoa_salient_partition_profile','hoa_salient_subband_counts'):self.assertNotIn(k,impl)

    def test_spatial_and_dynamic_methods_do_not_share_parameters_or_history(self):
        counts=[1,3,4,9,16];cases=[basis(counts,7,4,4,order=2,path='replace',dynamic=True),basis(counts,7,4,3,order=2,path='replace',dynamic=True)]
        previous=None
        for spatial_method in (0,1,2):
            for method in (0,1,2):
                opts=dict(order=2,path='replace',dynamic=True,counts=counts,spatial_method=spatial_method,method=method,subbands=3)
                _,rows=self.parsed(opts,cases)
                for row in rows:
                    side=row['hoa']['spatial']['salient'];dynamic=row['hoa']['dynamic_selection']
                    self.assertEqual(dynamic['subband_ends'],boundaries(3,method))
                    self.assertEqual([g['subband_ends'] for g in side['component_subbands']],[boundaries(n,spatial_method) for n in counts])
                identity=[(row['packet_sha256'],row['component_end_bit_offset'],row['hoa']['spatial']['salient']['descriptors']) for row in rows]
                if previous is not None:self.assertEqual(identity,previous)
                previous=identity

    def test_reserved_salient_method_and_unused_ambient_partition_are_distinct(self):
        root=self.path();opts=dict(spatial_method=3);bundle(root,[packet({})[0]],**opts)
        out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('hoa.parameter_1',p.stderr)
        import hoa_vectors as ambient
        raw=ambient.cookie();cfg=self.path();cfg.write_bytes(raw)
        parsed=json.loads(self.run_tool('parse-cookie',cfg).stdout);f=next(f for f in parsed['fields'] if f['name']=='components[0].hoa.parameter_1');at=f['bit_offset'];wire=''.join(format(v,'08b') for v in raw)
        payload=ambient.packet(ambient.excitation(15,gain=100))[0];reference=None
        for method in (0,1,2,3):
            root=self.path();ambient.bundle(root,[payload]);self.change_cookie(root,pack(wire[:at]+bits(method,2)+wire[at+2:]))
            out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,0,p.stderr)
            pcm=(out/'pcm.f32le').read_bytes();self.assertNotEqual(pcm,bytes(len(pcm)))
            if reference is not None:self.assertEqual(pcm,reference)
            reference=pcm
        for end in range(12,at//8+1):
            cfg=self.path();cfg.write_bytes(end.to_bytes(4,'big')+raw[4:end]);self.assertNotEqual(self.run_tool('parse-cookie',cfg).returncode,0)

    def test_new_layout_failures_preserve_packet_positions_and_markers(self):
        for f in state_fixtures()['fixtures']:
            for key,raw in f['errors'].items():
                root=self.path();bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(raw)],**f['options']);out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1,key);error=json.loads(p.stderr)['error']
                self.assertEqual(error['packet_index'],1);self.assertIn('bit_offset',error)
                self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())

    def test_new_capacity_fast_output_limits_and_overwrite(self):
        for _,opts,_ in native_controls():
            n=shape(opts['order'],opts['dynamic']);cap=18432 if n==9 else 32768;root=self.path()
            bundle(root,[pack('10001'+bits(cap+1,16))],**opts)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,1)
            self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        opts=dict(order=2,path='add',dynamic=True,spatial_method=1);raw=[packet({},**opts)[0]]*3
        for encode in (caf,mp4):
            source=self.path();source.write_bytes(encode(cookie(**opts),raw,channels=16)[0]);out=self.path()
            assert_hoa_fast(self,source,out);out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--frames',7);self.assertEqual(p.returncode,0,p.stderr);before=(out/'pcm.f32le').read_bytes()
            self.assertEqual(self.run_tool('decode-sq',source,'--out',out).returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path();bundle(root,raw*7,**opts);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())


if __name__=='__main__':unittest.main()
