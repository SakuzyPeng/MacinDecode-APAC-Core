"""Independent shared declaration references, syntax coverage and rejection controls."""
import json, subprocess, tempfile, unittest
from pathlib import Path
from portable_tools import required_binary
from hoa_shared_vectors import ambient,cookie,packet,bundle,marked
from hoa_shared_drc_vectors import metadata_controls
from hoa_passive_vectors import controls as passive_controls
from hoa_scene_vectors import variants as scene_variants

class SharedConfigTests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-shared-config-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
    def parse(self,extra):
        raw=cookie(components=[ambient(4)],drc=True,drc_configuration=dict(coefficients=[dict(sets=[{}])],**extra))
        path=self.root/'cookie.bin';path.write_bytes(raw)
        result=subprocess.run([str(self.binary),'parse-cookie',str(path)],capture_output=True,text=True)
        return raw,result
    def test_each_metadata_form_accounts_for_every_cookie_bit(self):
        for name,extra in metadata_controls():
            with self.subTest(name=name):
                raw,result=self.parse(extra);self.assertEqual(result.returncode,0,result.stderr)
                report=json.loads(result.stdout);self.assertEqual(report['status'],'complete');self.assertFalse(report['unknown_ranges'])
                cursor=0
                for field in sorted(report['fields'],key=lambda f:f['bit_offset']):
                    self.assertEqual(field['bit_offset'],cursor);cursor+=field['bit_length']
                self.assertEqual(cursor,len(raw)*8)
    def test_dependency_graph_and_ducking_recipients_are_bounded(self):
        cases=[('drc-dependency',dict(instructions=[dict(id=1,depends=2)])),('drc-dependency',dict(instructions=[dict(id=1,depends=2),dict(id=2,depends=1)])),('drc-ducking-groups',dict(instructions=[dict(effect=1024,indices=[0]*4)])),('drc-ducking-groups',dict(instructions=[dict(effect=1024,indices=[-1]*4)])),('drc-effect-combination',dict(instructions=[dict(effect=65535)]))]
        for kind,extra in cases:
            with self.subTest(kind=kind,extra=extra):
                raw,result=self.parse(extra);self.assertNotEqual(result.returncode,0)
                self.assertIn(kind,result.stderr)
    def test_layout_and_eq_references_are_checked(self):
        cases=[('drc-layout-count',dict(layout=dict(defined=2))),('eq-element-reference',dict(eq=dict(blocks=[[dict(index=1)]],elements=[dict(order=0,fir=[0])]))),('eq-block-reference',dict(eq=dict(instructions=[dict(cascades=[dict(blocks=[0])])]))),('eq-subband-reference',dict(eq=dict(instructions=[dict(subband_indices=[0])])))]
        for kind,extra in cases:
            with self.subTest(kind=kind):
                raw,result=self.parse(extra);self.assertNotEqual(result.returncode,0);self.assertIn(kind,result.stderr)

    def test_passive_renderer_cookies_account_for_every_bit(self):
        for name,metadata in passive_controls():
            with self.subTest(name=name):
                raw=cookie(components=[ambient(4)],renderer_metadata=metadata)
                path=self.root/'passive.bin';path.write_bytes(raw)
                result=subprocess.run([str(self.binary),'parse-cookie',str(path)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                report=json.loads(result.stdout);self.assertEqual(report['status'],'complete');cursor=0
                for field in sorted(report['fields'],key=lambda f:f['bit_offset']):
                    self.assertEqual(field['bit_offset'],cursor);cursor+=field['bit_length']
                self.assertEqual(cursor,len(raw)*8)

    def test_scene_qualification_distinguishes_full_routes_from_selection_and_gain(self):
        negative={'no-parameters','split-items','category-split','different-languages'}
        selected={'default','tag-words','split-groups','reverse-groups','equivalent-languages','inactive-languages','unused-language-alternative','category-one','preset-selection-explicit',*negative,'parameter-1-0','parameter-1-256','parameter-2-63','range-0-40-80'}
        for index,(name,definition) in enumerate(scene_variants(2)):
            if name not in selected:continue
            with self.subTest(name=name):
                cs=[ambient(1),ambient(4)];opts=dict(components=cs,scene=True,scene_definition=definition)
                raw=packet(dict(components=[marked(c,172) for c in cs],scene_update=True),**opts)[0]
                root=self.root/str(index);bundle(root,[raw],**opts)
                result=subprocess.run([str(self.binary),'decode-sq',str(root),'--out',str(root/'pcm')],capture_output=True,text=True)
                if name in negative or name=='parameter-1-0':
                    self.assertNotEqual(result.returncode,0);self.assertFalse((root/'pcm').exists())
                else:self.assertEqual(result.returncode,0,result.stderr)

    def test_missing_rate_context_is_not_an_explicit_rate_alias(self):
        base=cookie(components=[ambient(1)]);offset=96+16+6+4+1
        for index in range(13,64):
            raw=bytearray(base)
            for i in range(6):
                at=offset+i;raw[at//8]=(raw[at//8]&~(1<<(7-at%8)))|(((index>>(5-i))&1)<<(7-at%8))
            path=self.root/'rate.bin';path.write_bytes(raw)
            result=subprocess.run([str(self.binary),'parse-cookie',str(path)],capture_output=True,text=True)
            if index<16:
                report=json.loads(result.stdout);self.assertEqual(report['status'],'partial')
                self.assertNotIn('sample_rate_hz',report['derived']);self.assertIn('prior decoder state',report['diagnostics'][0]['message'])
            else:self.assertNotEqual(result.returncode,0);self.assertIn('sample-rate-index',result.stderr)

    def test_graph_parents_and_reference_metadata_boundaries_are_explicit(self):
        for graph,kind in [([dict(parent=1)],'scene-parent-cycle'),([dict(parent=2)],'scene-parent')]:
            path=self.root/'graph.bin';path.write_bytes(cookie(components=[ambient(1)],scene_graph=graph))
            result=subprocess.run([str(self.binary),'parse-cookie',str(path)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertIn(kind,result.stderr)
        metadata=dict(parameters=[dict(id=7,data='00100000000')])
        # Complete type-2 HRTF resource declaration is a known reference rejection.
        from hoa_passive_vectors import global_controls
        metadata['parameters'][0]['data']=next(data for name,kind,data in global_controls() if name=='hrtf-resource')
        path=self.root/'reference-boundary.bin';path.write_bytes(cookie(components=[ambient(1)],renderer_metadata=metadata))
        result=subprocess.run([str(self.binary),'parse-cookie',str(path)],capture_output=True,text=True)
        report=json.loads(result.stdout);self.assertEqual(report['status'],'partial');self.assertIn('reference',report['diagnostics'][0]['message'])

if __name__=='__main__':unittest.main()
