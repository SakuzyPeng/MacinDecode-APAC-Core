"""Portable channel grammar, mapping, range and failure contracts."""
import copy,json,struct,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from channel_vectors import LAYOUTS,cookie,packet,bundle,excitation,configuration,manifest
from caf_vectors import encode
from validate_channels import check,compare

class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-channels-test-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def prepare(self,name,n,cases,rate=48000,**options):
        generated=[packet(c,n,rate,**options) for c in cases];path=self.root/name;bundle(path,[r for r,_ in generated],n,rate,**options);return path,generated
    def test_every_output_channel_is_isolated_and_packet_boundaries_match(self):
        for n in LAYOUTS:
            cases=[]
            for ch in range(n):cases.extend([excitation(n,ch),dict(elements=[None]*len(LAYOUTS[n][2]))])
            path,generated=self.prepare(str(n),n,cases)
            result=self.run_tool('parse-packets',path,'--depth','channels','--packets',len(cases),'--output',path/'parsed.jsonl');self.assertEqual(result.returncode,0,result.stderr)
            rows=[json.loads(l)['report'] for l in (path/'parsed.jsonl').read_text().splitlines()]
            for row,(_,truth) in zip(rows,generated):check(row,truth,n)
            result=self.run_tool('decode-sq',path,'--out',path/'pcm');self.assertEqual(result.returncode,0,result.stderr)
            pcm=(path/'pcm/pcm.f32le').read_bytes();values=struct.unpack('<'+str(len(pcm)//4)+'f',pcm)
            for ch in range(n):
                selected=values[ch*2048*n:(ch+1)*2048*n]
                self.assertTrue(any(selected[ch::n]))
                for other in range(n):
                    if other!=ch:self.assertTrue(all(v==0 for v in selected[other::n]))
    def test_sce_zero_bands_still_consume_lsf_words(self):
        for block,mask in ((0,0),(1,0),(2,0),(2,0x55),(2,0x7f),(3,0)):
            path,generated=self.prepare(f'{block}-{mask}',1,[dict(elements=[dict(block=block,grouping=mask,bwe2=dict(lsf=[511,511],gains=[]))])])
            p=self.run_tool('parse-packets',path,'--depth','channels','--output',path/'report.jsonl');self.assertEqual(p.returncode,0,p.stderr)
            row=json.loads((path/'report.jsonl').read_text())['report'];check(row,generated[0][1],1)
            data=row['elements'][0]['bwe2'];self.assertEqual(data['end_bit_offset']-data['start_bit_offset'],19)
            self.assertEqual(data['channels'][0]['parameters']['gain_indices'],[])
            self.assertFalse(row['elements'][0]['channels_after_bwe2'][0]['processing_applied'])
    def test_caf_and_bundle_ranges_keep_multichannel_priming_exact(self):
        for n in LAYOUTS:
            for rate in (48000,44100):
                options=dict(scene=True,drc=True,rich=True);seq=[excitation(n,0),excitation(n,n-1,(2,0x55)),{},dict(elements=[None]*len(LAYOUTS[n][2])),{}]
                path,parts=self.prepare(f'{n}-{rate}',n,seq,rate,**options)
                raw,truth=encode(cookie(n,rate,**options),[p for p,_ in parts],rate,priming=1031,remainder=29,variant=n,channels=n)
                caf=path/'input.unknown';caf.write_bytes(raw)
                m=json.loads((path/'manifest.json').read_text());m['file']['packet_table']['value']=truth['packet_table'];(path/'manifest.json').write_text(json.dumps(m))
                full=None
                for label,start,count in [('all',0,10000),('head',0,31),('middle',1111,1127),('tail',truth['packet_table']['valid_frames']-19,4096),('eof',truth['packet_table']['valid_frames'],1)]:
                    outputs=[]
                    for kind,src in [('caf',caf),('bundle',path)]:
                        dest=path/f'{kind}-{label}';p=self.run_tool('decode-sq',src,'--out',dest,'--start-frame',start,'--frames',count);self.assertEqual(p.returncode,0,p.stderr)
                        r=json.loads(p.stdout);self.assertEqual(r['pcm']['channels'],n);self.assertEqual(r['pcm']['source_packet_table'],truth['packet_table'])
                        outputs.append((dest/'pcm.f32le').read_bytes())
                    self.assertEqual(*outputs)
                    if label=='all':full=outputs[0]
                    self.assertEqual(outputs[0],full[start*n*4:(start+min(count,truth['packet_table']['valid_frames']-start))*n*4])
    def test_old_depths_keep_their_stereo_restriction(self):
        path,_=self.prepare('mono',1,[{}])
        for depth in ('prefix','spectrum','cac','tns','bwe2','drc','packet'):
            p=self.run_tool('parse-packets',path,'--depth',depth,'--output',path/(depth+'.jsonl'));self.assertEqual(p.returncode,2,p.stderr)
        p=self.run_tool('parse-packets',path,'--depth','channels','--output',path/'channels.jsonl');self.assertEqual(p.returncode,0,p.stderr)
    def test_last_element_errors_are_identified_and_outputs_stay_incomplete(self):
        fixtures=json.loads((Path(__file__).resolve().parents[1]/'data/channel-state-fixtures-v1.json').read_text())['fixtures']
        for fixture in fixtures:
            n=fixture['channels'];raw=bytes.fromhex(fixture['last_element_error']);path=self.root/str(n)
            bundle(path,[raw],n,drc=True,rich=True)
            p=self.run_tool('parse-packets',path,'--depth','channels','--output',path/'lr.jsonl');self.assertEqual(p.returncode,2,p.stderr)
            report=json.loads((path/'lr.jsonl').read_text())['report'];self.assertFalse(report['packet_complete']);self.assertIn('lrvq_element_',report['stop_reason'])
            p=self.run_tool('decode-sq',path,'--out',path/'pcm');self.assertEqual(p.returncode,1,p.stderr);self.assertTrue((path/'pcm/.incomplete.json').exists())
            late=bytes.fromhex(fixture['late_drc_error']);bad=self.root/f'late-{n}';bundle(bad,[late],n,drc=True,rich=True)
            p=self.run_tool('parse-packets',bad,'--depth','channels','--output',bad/'bad.jsonl');self.assertEqual(p.returncode,1,p.stderr);self.assertTrue((bad/'bad.jsonl.incomplete').exists())
    def test_each_layout_capacity_rejects_oversize_and_nested_preroll(self):
        from spectrum_vectors import bits,pack
        for n,maximum in ((1,2048),(2,4096),(6,12288),(8,16384)):
            for name,raw in [('oversize',pack('10001'+bits(maximum+1,16))),('zero',pack('10001'+bits(0,16))),('nested',pack('10001'+bits(1,16)+'000'+'10000000'+'00000000'))]:
                path=self.root/f'{n}-{name}';bundle(path,[raw],n)
                p=self.run_tool('parse-packets',path,'--depth','channels','--output',path/'error.jsonl');self.assertEqual(p.returncode,1,p.stderr)
                e=json.loads((path/'error.jsonl').read_text())['error'];self.assertIn(e['kind'],('preroll-size','nested-preroll'))
    def test_truncation_markers_and_output_limit_are_not_silent(self):
        n=8;case=excitation(n,7,(2,0x55));raw,truth=packet(case,n,drc=True,rich=True)
        values=[raw[:i] for i in range(1,len(raw)-1)];path=self.root/'truncated';bundle(path,values,n,drc=True,rich=True)
        p=self.run_tool('parse-packets',path,'--depth','channels','--packets',len(values),'--output',path/'errors.jsonl');self.assertEqual(p.returncode,1,p.stderr)
        rows=[json.loads(l) for l in (path/'errors.jsonl').read_text().splitlines()];self.assertTrue(all(r['status']=='error' for r in rows));self.assertTrue((path/'errors.jsonl.incomplete').exists())
        self.assertTrue(any(r['error'].get('element_index')==4 for r in rows))
        large=self.root/'large';bundle(large,[raw]*40,n,drc=True,rich=True)
        p=self.run_tool('decode-sq',large,'--out',large/'pcm','--max-output-mib',1);self.assertEqual(p.returncode,1);self.assertTrue((large/'pcm/.incomplete.json').exists())
        before=(path/'errors.jsonl').read_bytes();self.assertEqual(self.run_tool('parse-packets',path,'--depth','channels','--output',path/'errors.jsonl').returncode,1);self.assertEqual(before,(path/'errors.jsonl').read_bytes())
    def test_configuration_rejection_lists_actual_fields(self):
        from spectrum_vectors import bits,pack
        original=cookie(6)
        for bit,width,value,label in [(118,4,0,'level_id'),(176,16,101,'layout_family')]:
            # Locate the field from the existing independent cookie parser report,
            # only for mutation targeting; expected acceptance remains fixed.
            file=self.root/f'{label}.bin';file.write_bytes(original)
            report=json.loads(self.run_tool('parse-cookie',file).stdout)
            field=next(f for f in report['fields'] if f['name'].endswith(label));bit=field['bit_offset'];width=field['bit_length']
            wire=''.join(bits(v,8) for v in original);changed=pack(wire[:bit]+bits(value,width)+wire[bit+width:])
            path=self.root/label;bundle(path,[packet({},6)[0]],6);(path/'cookie.bin').write_bytes(changed)
            m=json.loads((path/'manifest.json').read_text());import hashlib;m['file']['cookie']['value']['sha256']=hashlib.sha256(changed).hexdigest();(path/'manifest.json').write_text(json.dumps(m))
            p=self.run_tool('decode-sq',path,'--out',path/'pcm');self.assertEqual(p.returncode,1);self.assertIn(label,p.stderr);self.assertIn('cookie bit',p.stderr)
    def test_channel_history_is_warmed_before_requested_parse_window(self):
        options=dict(drc=True,rich=True);cases=[dict(drc=dict(header=True,loudness_value=1)),dict(drc=dict(header=True,metadata_only=True,loudness_value=255)),{}]
        path,_=self.prepare('history',6,cases,**options)
        p=self.run_tool('parse-packets',path,'--depth','channels','--start-packet',2,'--packets',1,'--output',path/'selected.jsonl');self.assertEqual(p.returncode,0,p.stderr)
        r=json.loads((path/'selected.jsonl').read_text())['report'];self.assertTrue(r['drc_history_sufficient']);self.assertEqual(r['drc']['configuration']['source'],'packet')
        values=[f['value'] for f in r['drc']['configuration']['loudness_metadata'] if f['name'].endswith('.value_a_encoded')];self.assertEqual(values,[255])
    def test_math_checker_rejects_injected_pcm_error(self):
        metrics=compare([0.,0.01],[0.,0.],dict(channels=8,stage='pcm'));self.assertFalse(metrics['passed']);self.assertEqual(metrics['first_failure']['index'],1)
    def test_frozen_vector_identity_is_exact(self):
        from validate_channels import MANIFEST,COUNT
        m=manifest();self.assertEqual(len(m['sequences']),COUNT);self.assertEqual(json.loads(MANIFEST.read_text()),m)
