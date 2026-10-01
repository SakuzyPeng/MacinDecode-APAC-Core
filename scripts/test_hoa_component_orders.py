"""Per-component dimensions, omission intersections, metadata and failure contracts."""
import json,unittest
import test_hoa_salient_partition as prior
from hoa_component_orders_vectors import cookie,packet,bundle,basis,descriptors,native_controls,state_fixtures,component_information,PROFILE,STATE_PROFILE,BACKEND
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class ComponentOrdersTests(unittest.TestCase):
    setUp=prior.PartitionTests.setUp
    path=prior.PartitionTests.path
    run_tool=prior.PartitionTests.run_tool
    change_cookie=prior.PartitionTests.change_cookie
    parsed=prior.PartitionTests.parsed

    def test_each_component_order_position_and_fixed_sixteen_output_domain(self):
        patterns=[[3]*5,[2]*5]+[[2 if s==i else 3 for s in range(5)] for i in range(5)]+[[2,3,2,3,2],[3,2,3,2,3]]
        for i,orders in enumerate(patterns):
            opts=dict(order=3,component_orders=orders,counts=[1,3,4,9,16],rate=44100 if i%2 else 48000,spatial_method=i%3)
            root,rows=self.parsed(opts,[{}]);r=rows[0];side=r['hoa']['spatial']['salient']
            self.assertEqual((r['channel_count'],r['hoa']['order'],r['hoa']['coefficient_count']),(16,3,16))
            self.assertTrue(all(len(d['restored'])==(orders[d['component_index']]+1)**2 for d in side['descriptors']))
            if orders!=[3]*5:self.assertEqual(side['component_orders'],component_information(orders));self.assertEqual(r['hoa']['numeric_profile'],PROFILE)
            else:self.assertNotIn('component_orders',side)
            expected=[];cookie(**opts,_order_fields=expected);parsed=json.loads(self.run_tool('parse-cookie',root/'cookie.bin').stdout)
            for truth in expected:
                actual=next(f for f in parsed['fields'] if f['name']==truth['name'])
                for k,v in truth.items():self.assertEqual(actual[k],v)

    def test_omission_intersects_each_dimension_and_clears_only_valid_history(self):
        orders=[2,3,2,3,2];counts=[1]*5;selection=[8,9,10,15]
        for path in ('replace','add'):
            opts=dict(order=3,path=path,component_orders=orders,counts=counts,selection=selection,transform=4)
            cases=[dict(descriptors=descriptors(counts,mode,order=3,component_orders=orders),transform_index=i%4) for i,mode in enumerate((0,1,2,4,3,5,3))]
            _,rows=self.parsed(opts,cases)
            for row in rows:
                for d in row['hoa']['spatial']['salient']['descriptors']:
                    n=(orders[d['component_index']]+1)**2
                    omitted=[k for k in selection if k<n] if path=='replace' and d['mode']<4 else []
                    self.assertEqual(d['ambient_omitted_coefficients'],omitted)
                    self.assertTrue(all(k<n and k not in omitted for k in d['coded_coefficient_indices']))
                    self.assertTrue(all(d['restored'][k]==0 for k in omitted))

    def test_all_order_two_keeps_sixteen_pcm_and_composite_metadata(self):
        _,opts,cases=list(native_controls())[-1];root,rows=self.parsed(opts,cases)
        for row in rows:
            self.assertTrue(all(all(v==0 for v in c['scaled']) for c in row['hoa']['channels_after_hoa'][9:]))
        out=self.path();p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);impl=r['pcm']['decoder_settings']['implementation']['value']
        self.assertEqual((r['pcm']['channels'],r['backend'],r['packet_state_profile']),(16,BACKEND,STATE_PROFILE))
        self.assertEqual(impl['hoa_descriptor_numeric_profile'],PROFILE);self.assertEqual(impl['hoa_salient_component_orders'],[2]*5)
        self.assertEqual(impl['hoa_salient_components'],component_information([2]*5));self.assertNotIn('hoa_format_sha256',impl)
        raw=(out/'pcm.f32le').read_bytes();self.assertEqual(len(raw),len(cases)*16*1024*4)
        self.assertTrue(all(raw[i+9*4:i+16*4]==bytes(7*4) for i in range(0,len(raw),16*4)))

    def test_legacy_uniform_order_bytes_fields_and_stateful_parse_remain_unchanged(self):
        for path in ('salient','replace','add'):
            opts=dict(order=3,path=path,counts=[4]*5);c=basis([4]*5,8,0,4,path=path)
            self.assertEqual(cookie(**opts),cookie(**opts,component_orders=[3]*5));self.assertEqual(packet(c,**opts),packet(c,**opts,component_orders=[3]*5))
            root,rows=self.parsed(opts,[c,{}]);self.assertNotIn('component_orders',rows[0]['hoa']['spatial']['salient'])
            p=self.run_tool('decode-sq',root,'--out',root/'pcm');self.assertEqual(p.returncode,0,p.stderr);impl=json.loads(p.stdout)['pcm']['decoder_settings']['implementation']['value']
            self.assertIn('hoa_format_sha256',impl);self.assertNotIn('hoa_salient_component_orders',impl);self.assertNotEqual(impl['backend'],BACKEND)
        _,opts,cases=next(native_controls());root,rows=self.parsed(opts,cases)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--start-packet',len(cases)-1,'--packets',1,'--output',root/'last')
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads((root/'last').read_text())['report'],rows[-1])

    def test_unsupported_component_orders_stop_before_output(self):
        for bad in (0,):
            root=self.path();opts=dict(component_orders=[2,3,2,3,3]);bundle(root,[packet({},**opts)[0]],**opts)
            self.change_cookie(root,cookie(component_orders=[bad,3,2,3,3]));out=self.path()
            p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('salient[0].order',p.stderr)
        for dynamic in (False,True):
            root=self.path();opts=dict(order=2,dynamic=dynamic);bundle(root,[packet({},**opts)[0]],**opts)
            self.change_cookie(root,cookie(**opts,component_orders=[2,3,2,2,2]));out=self.path()
            p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertFalse(out.exists());self.assertIn('hoa-order',p.stderr)

    def test_late_failures_retain_error_coordinates_and_failure_markers(self):
        for f in state_fixtures()['fixtures']:
            for key,raw in f['errors'].items():
                root=self.path();bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(raw)],**f['options']);out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1,key);error=json.loads(p.stderr)['error']
                self.assertEqual(error['packet_index'],1);self.assertIn('bit_offset',error)
                self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())

    def test_capacity_fast_limits_and_overwrite_for_component_orders(self):
        for _,opts,_ in native_controls():
            root=self.path();bundle(root,[pack('10001'+bits(32769,16))],**opts)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,1)
            self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        opts=dict(order=3,path='add',component_orders=[2,3,3,2,2]);raw=[packet({},**opts)[0]]*3
        for encode in (caf,mp4):
            source=self.path();source.write_bytes(encode(cookie(**opts),raw,channels=16)[0]);out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--access','fast');self.assertEqual(p.returncode,1);self.assertFalse(out.exists())
            p=self.run_tool('decode-sq',source,'--out',out,'--frames',7);self.assertEqual(p.returncode,0,p.stderr);before=(out/'pcm.f32le').read_bytes()
            self.assertEqual(self.run_tool('decode-sq',source,'--out',out).returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path();bundle(root,raw*7,**opts);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())

    def test_ragged_descriptor_failure_coordinate_uses_actual_widths(self):
        from validate_hoa_salient_subbands import measure
        counts=[1,3,4,9,16];orders=[2,3,2,3,3];dimensions=[(o+1)**2 for o in orders]
        rows=[dict(component_index=s,subband_index=b) for s,n in enumerate(counts) for b in range(n)]
        widths=[dimensions[d['component_index']] for d in rows];i=sum(counts[s]*dimensions[s] for s in range(3))+8*16+15
        expected=[0.]*sum(widths);actual=expected.copy();actual[i]=.01;metric=dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)
        with self.assertRaises(RuntimeError):measure(metric,actual,expected,dict(case=0,packet=0,role='current',stage='descriptors'),0,16,16,True,rows,widths)
        self.assertEqual((metric['first_failure']['component'],metric['first_failure']['subband'],metric['first_failure']['slot']),(3,8,15))


if __name__=='__main__':unittest.main()
