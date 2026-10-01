"""Carrier basis isolation, bounded opaque payloads and file/output failure contracts."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
import hoa_transport_vectors as v
import test_hoa_salient_subbands as helpers
from portable_tools import required_binary
from spectrum_vectors import bits


class TransportTests(unittest.TestCase):
    path=helpers.SpatialSubbandTests.path
    run_tool=helpers.SpatialSubbandTests.run_tool
    change_cookie=helpers.SpatialSubbandTests.change_cookie

    def setUp(self):
        self.binary=required_binary();root=Path(__file__).resolve().parents[1]/'artifacts';root.mkdir(exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='hoa-transports-tests-',dir=root));self.index=0
        self.addCleanup(self.cleanup)

    def cleanup(self):
        result=self._outcome.result
        if any(test is self for test,_ in result.failures+result.errors):
            print('retained failed transport inputs: '+str(self.root))
        else:shutil.rmtree(self.root)

    def parsed(self,opts,cases):
        root=self.path();v.bundle(root,[v.packet(c,**opts)[0] for c in cases],**opts)
        p=self.run_tool('parse-packets',root,'--depth','hoa','--output',root/'parsed')
        self.assertEqual(p.returncode,0,p.stderr)
        return root,[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]

    def test_each_cpe_channel_maps_to_its_carrier_and_recovery_domain(self):
        for name in ('pair','replace'):
            _,opts,_=next(x for x in v.native_controls() if x[0]==name)
            types=opts['tce_types'];total=sum(v.width(t) for t in types);ambient=opts['ambient_count'];cases=[]
            for active in range(total):
                specs=[];first=0
                for typ in types:
                    spec=None
                    if first<=active<first+v.width(typ):
                        signal={0:(1,[1,0,0,0],100)}
                        spec=dict(independent=True,gain=100,**{'left' if active==first else 'right':signal}) if typ==1 else dict(gain=100,bands=signal)
                    specs.append(spec);first+=v.width(typ)
                case=dict(elements=specs)
                if opts['counts']:case['descriptors']=v.descriptors(opts['counts'],0,8,max(0,active-ambient),order=opts['order'])
                cases.append(case)
            _,rows=self.parsed(opts,cases)
            for active,row in enumerate(rows):
                target=(opts.get('selection') or list(range(ambient)))[active] if active<ambient else 8
                for channel in row['hoa']['channels_after_hoa']:
                    expected=(1. if active<ambient else .25) if channel['acn_index']==target else 0.
                    self.assertEqual(channel['scaled'][0],expected,(name,active,channel['acn_index']))
                    self.assertFalse(any(channel['scaled'][1:]))

    def test_opaque_extension_content_changes_reports_without_changing_pcm(self):
        _,opts,cases=next(x for x in v.native_controls() if x[0]=='extension')
        digests=[];pcm=[]
        for payload in ([0,1,255],[255,0,1]):
            case=dict(cases[0],elements=[dict(payload=payload,parameter=42),*cases[0]['elements'][1:]])
            root,rows=self.parsed(opts,[case])
            digests.append(rows[0]['elements'][0]['extension']['payload_sha256'])
            p=self.run_tool('decode-sq',root,'--out',root/'pcm');self.assertEqual(p.returncode,0,p.stderr)
            pcm.append((root/'pcm/pcm.f32le').read_bytes())
        self.assertNotEqual(*digests);self.assertEqual(*pcm)
        for body in ('1'+bits(1,7),'0'+bits(2,7)+bits(255,8),'0'+bits(2,7)+bits(255,8)+bits(1,16)):
            case=dict(cases[0],elements=[dict(body=body),*cases[0]['elements'][1:]])
            root=self.path();v.bundle(root,[v.packet(case,**opts)[0]],**opts);out=self.path()
            p=self.run_tool('decode-sq',root,'--out',out)
            self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file())
            self.assertFalse((out/'decode-sq.json').exists())

    def test_failure_coordinates_and_output_protection(self):
        fixtures=v.state_fixtures()
        for f in fixtures['fixtures']:
            if f['name'] not in ('pair','add','dynamic','many-extensions'):continue
            for name,raw in f['errors'].items():
                root=self.path();v.bundle(root,[bytes.fromhex(f['first']),bytes.fromhex(raw)],**f['options']);out=self.path()
                p=self.run_tool('decode-sq',root,'--out',out)
                self.assertEqual(p.returncode,1,(f['name'],name));error=json.loads(p.stderr)['error']
                self.assertEqual(error['packet_index'],1);self.assertIn('bit_offset',error)
                self.assertTrue((out/'.incomplete.json').is_file());self.assertFalse((out/'decode-sq.json').exists())
        _,opts,cases=next(x for x in v.native_controls() if x[0]=='dynamic');raw=v.packet(cases[0],**opts)[0]
        root=self.path();v.bundle(root,[raw]*20,**opts);out=self.path()
        p=self.run_tool('decode-sq',root,'--out',out,'--max-output-mib',1)
        self.assertEqual(p.returncode,1);self.assertTrue((out/'.incomplete.json').is_file())
        out=self.path();p=self.run_tool('decode-sq',root,'--out',out,'--frames',7);self.assertEqual(p.returncode,0,p.stderr)
        saved=(out/'pcm.f32le').read_bytes()
        self.assertEqual(self.run_tool('decode-sq',root,'--out',out).returncode,1)
        self.assertEqual((out/'pcm.f32le').read_bytes(),saved)
        for raw in fixtures['invalid']:
            source=self.path();v.bundle(source,[v.packet({},**opts)[0]],**opts);self.change_cookie(source,bytes.fromhex(raw));out=self.path()
            self.assertEqual(self.run_tool('decode-sq',source,'--out',out).returncode,1);self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
