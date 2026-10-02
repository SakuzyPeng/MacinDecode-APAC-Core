"""First-order domains, full omissions, directional payloads and complete dynamic mappings."""
from portable_tools import assert_hoa_fast
import json,unittest
import test_hoa_salient_partition as helpers
from hoa_order1_vectors import cookie,packet,bundle,basis,descriptors,native_controls,state_fixtures,component_information,PROFILE
from hoa_dynamic_vectors import maps
from spectrum_vectors import bits,pack
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


class Order1Tests(unittest.TestCase):
    setUp=helpers.PartitionTests.setUp
    path=helpers.PartitionTests.path
    run_tool=helpers.PartitionTests.run_tool
    change_cookie=helpers.PartitionTests.change_cookie
    parsed=helpers.PartitionTests.parsed

    def test_three_domains_and_three_recovery_strategies_keep_actual_dimensions(self):
        for i,(order,dynamic) in enumerate(((2,False),(3,False),(2,True))):
            for path in ('salient','replace','add'):
                orders=[1,order,1,order,1];opts=dict(order=order,dynamic=dynamic,path=path,rate=44100 if i%2 else 48000,component_orders=orders,counts=[1,3,4,9,16],spatial_method=i)
                _,rows=self.parsed(opts,[{}]);r=rows[0];h=r['hoa'];side=h['spatial']['salient']
                self.assertEqual((h['order'],h['coefficient_count'],r['channel_count']),(order,(order+1)**2,16 if dynamic else (order+1)**2))
                self.assertEqual(side['component_orders'],component_information(orders));self.assertEqual(side['order1_profile'],PROFILE)
                self.assertTrue(all(len(d['restored'])==(orders[d['component_index']]+1)**2 for d in side['descriptors']))
                if dynamic:self.assertEqual(len(h['dynamic_selection']['mappings']),8);self.assertEqual(len(h['dynamic_selection']['before_selection']),9)

    def test_each_first_order_basis_and_carrier_stays_in_its_four_coefficient_domain(self):
        for k in range(4):
            for sc in range(5):
                opts=dict(order=3,component_orders=[1]*5,counts=[1]*5)
                _,rows=self.parsed(opts,[basis([1]*5,k,sc,0,component_orders=[1]*5)])
                spectra=rows[0]['hoa']['channels_after_hoa']
                self.assertTrue(any(v!=0 for v in spectra[k]['scaled']))
                self.assertTrue(all(all(v==0 for v in c['scaled']) for i,c in enumerate(spectra) if i!=k))

    def test_direction_angles_are_read_and_reported_but_explicit_values_replace_all_four(self):
        opts=dict(order=3,component_orders=[1]*5,counts=[1]*5);pcms=[];reports=[]
        for angles in ((0,0),(511,255)):
            c=basis([1]*5,3,0,5,component_orders=[1]*5)
            for row in c['descriptors']:row[0].update(angles=angles,quantized=[40,44,48,52])
            follow=basis([1]*5,3,0,3,component_orders=[1]*5)
            root,rows=self.parsed(opts,[c,follow]);reports.append(rows)
            for d in rows[0]['hoa']['spatial']['salient']['descriptors']:
                self.assertEqual(d['restored'],[.25,.375,.5,.625]);self.assertEqual((d['azimuth_degrees'],d['elevation_offset_degrees']),angles)
            p=self.run_tool('decode-sq',root,'--out',root/'pcm');self.assertEqual(p.returncode,0,p.stderr);pcms.append((root/'pcm/pcm.f32le').read_bytes())
            self.assertEqual(rows[1]['hoa']['spatial']['salient']['history_frame_sha256'],rows[0]['packet_sha256'])
        self.assertEqual(pcms[0],pcms[1]);self.assertNotEqual(reports[0][0]['packet_sha256'],reports[1][0]['packet_sha256'])

    def test_fully_omitted_descriptions_have_empty_wire_values_and_clear_history(self):
        for count in (1,16):
            opts=dict(order=3,path='replace',component_orders=[1]*5,counts=[count]*5,selection=[0,1,2,3],transform=4)
            cases=[]
            for i,mode in enumerate((4,3,5,3,0)):
                rows=descriptors([count]*5,mode,3,0,component_orders=[1]*5)
                if mode==5:
                    for row in rows:
                        for d in row:d['quantized']=[40,44,48,52]
                cases.append(dict(descriptors=rows,global_mode=mode,transform_index=i%4))
            _,reports=self.parsed(opts,cases)
            for report,mode in zip(reports,(4,3,5,3,0)):
                ds=report['hoa']['spatial']['salient']['descriptors'];self.assertEqual(len(ds),5*count)
                for d in ds:
                    if mode in (0,3):
                        self.assertEqual(d['quantized'],[]);self.assertEqual(d['signs_positive'],[]);self.assertEqual(d['coded_coefficient_indices'],[])
                        self.assertEqual(d['ambient_omitted_coefficients'],[0,1,2,3]);self.assertEqual(d['restored'],[0.]*4)
                        self.assertEqual(d['start_bit_offset'],d['end_bit_offset'])
                    else:self.assertEqual(len(d['quantized']),4);self.assertEqual(d['ambient_omitted_coefficients'],[])

    def test_zero_spectra_still_require_nine_targets_in_all_eight_dynamic_rows(self):
        opts=dict(order=2,path='replace',dynamic=True,component_orders=[1]*5,counts=[1]*5,selection=[0,1,2,3],subbands=1)
        for listing in (True,False):
            raw,truth=packet(dict(list_mode=listing,mappings=maps(0,listing)),**opts)
            row=truth['dynamic_selection']['mappings'][7];wire=''.join(format(v,'08b') for v in raw)
            at=row['start_bit_offset']+(4 if listing else 0);value=bits(row['target_acn_indices'][0],4) if listing else bits(15,16)
            broken=pack(wire[:at]+value+wire[at+len(value):]);root=self.path();bundle(root,[broken],**opts);out=self.path()
            p=self.run_tool('decode-sq',root,'--out',out);self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())

    def test_unsupported_zero_and_orders_above_internal_dimension_stop_before_output(self):
        for order,dynamic,orders in ((3,False,[0,2,3,1,3]),(2,False,[1,3,1,2,1]),(2,True,[1,3,1,2,1])):
            opts=dict(order=order,dynamic=dynamic);root=self.path();bundle(root,[packet({},**opts)[0]],**opts)
            self.change_cookie(root,cookie(**opts,component_orders=orders));out=self.path();p=self.run_tool('decode-sq',root,'--out',out)
            self.assertEqual(p.returncode,1);self.assertFalse(out.exists())
        raw=self.path();valid=cookie(order=2,component_orders=[1]*5);raw.write_bytes(valid)
        fields=json.loads(self.run_tool('parse-cookie',raw).stdout)['fields'];field=next(f for f in fields if f['name']=='components[0].hoa.order')
        at=field['bit_offset'];width=field['bit_length'];wire=''.join(format(v,'08b') for v in valid)
        raw.write_bytes(pack(wire[:at]+bits(1,width)+wire[at+width:]));p=self.run_tool('parse-cookie',raw)
        self.assertNotEqual(p.returncode,0);self.assertIn('hoa-component-count',p.stderr)

    def test_late_errors_capacities_and_output_guards(self):
        for f in state_fixtures()['fixtures']:
            for key,raw in f['errors'].items():
                root=self.path();bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(raw)],**f['options']);out=self.path();p=self.run_tool('decode-sq',root,'--out',out)
                self.assertEqual(p.returncode,1,key);e=json.loads(p.stderr)['error'];self.assertEqual(e['packet_index'],1);self.assertIn('bit_offset',e)
                self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())
        for _,opts,_ in native_controls():
            cap=18432 if opts['order']==2 and not opts['dynamic'] else 32768;root=self.path();bundle(root,[pack('10001'+bits(cap+1,16))],**opts)
            p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed');self.assertEqual(p.returncode,1);self.assertEqual(json.loads((root/'parsed').read_text())['error']['kind'],'preroll-size')
        opts=dict(order=2,dynamic=True,path='add',component_orders=[1]*5);raw=[packet({},**opts)[0]]*3
        for encode in (caf,mp4):
            source=self.path();source.write_bytes(encode(cookie(**opts),raw,channels=16)[0]);out=self.path()
            assert_hoa_fast(self,source,out);out=self.path()
            p=self.run_tool('decode-sq',source,'--out',out,'--frames',7);self.assertEqual(p.returncode,0,p.stderr);before=(out/'pcm.f32le').read_bytes()
            self.assertEqual(self.run_tool('decode-sq',source,'--out',out).returncode,1);self.assertEqual(before,(out/'pcm.f32le').read_bytes())
        root=self.path();bundle(root,raw*7,**opts);out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())


if __name__=='__main__':unittest.main()
