"""Portable MP4 boundary, failure, resource and timeline contracts."""
import json,os,struct,subprocess,tempfile,unittest
from pathlib import Path
from portable_tools import required_binary
from mp4_vectors import encode,cookie,packet,emit,box,manifest

class Mp4Tests(unittest.TestCase):
    def setUp(self):
        self.binary=required_binary();tmp=tempfile.TemporaryDirectory(prefix='apac-mp4-test-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name);self.n=0
    def run_tool(self,*args):return subprocess.run([str(self.binary),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
    def run_mp4(self,raw,*args):
        self.n+=1;src=self.root/f'{self.n}.other';src.write_bytes(raw);out=self.root/f'out{self.n}'
        return self.run_tool('decode-sq',src,'--out',out,*args),out,src
    def raw(self,**kw):return encode(cookie(2),[packet({},2)[0]]*5,**kw)
    def mutate(self,raw,offset,value):
        raw=bytearray(raw);raw[offset:offset+len(value)]=value;return raw
    def rejected(self,raw,*args):
        p,out,_=self.run_mp4(raw,*args);self.assertEqual(p.returncode,1,p.stdout)
        error=json.loads(p.stderr)['error'];self.assertFalse((out/'decode-sq.json').exists());return error,out
    def test_layouts_and_container_variants_have_independent_offsets(self):
        from validate_mp4 import check_input
        for n in (1,2,6,8):
            for variant in (0,1,2,4,7,8,31,63,128,511):
                raw,truth,_=encode(cookie(n),[packet({},n)[0]]*5,channels=n,variant=variant)
                p,out,_=self.run_mp4(raw);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout);check_input(r['input'],truth)
                self.assertEqual(r['pcm']['channels'],n);self.assertEqual((out/'pcm.f32le').stat().st_size,5*1024*n*4)
    def test_every_byte_truncation_reports_container_position(self):
        raw,_,_=self.raw()
        for end in range(len(raw)):
            error,_=self.rejected(raw[:end]);self.assertIn('byte_offset',error,str(end));self.assertIn('chunk_type',error)
    def test_missing_duplicate_and_unsupported_boxes(self):
        raw,truth,_=self.raw();boxes=truth['boxes']
        for name in ('ftyp','mvhd','tkhd','elst','mdhd','hdlr','smhd','dref','stsd','stsz','stsc','stco','stts'):
            self.rejected(self.mutate(raw,boxes[name]['offset']+4,b'free'))
        for name in ('moof','mvex','cmov','sinf','senc','saiz','saio','stz2'):
            self.rejected(raw+emit(box(name.encode(),b'')))
        for name in ('ftyp','moov'):
            b=boxes[name];self.rejected(raw+raw[b['offset']:b['offset']+b['bytes']])
        # Duplicate each required leaf in its own parent, adjusting all containing boxes.
        for name in ('mvhd','tkhd','elst','mdhd','hdlr','smhd','dref','stsd','stsz','stsc','stco','stts'):
            b=boxes[name];pos=b['offset']+b['bytes'];extra=raw[b['offset']:pos];bad=bytearray(raw[:pos]+extra+raw[pos:])
            for parent in boxes.values():
                if parent['offset']<b['offset'] and parent['offset']+parent['bytes']>=pos:struct.pack_into('>I',bad,parent['offset'],parent['bytes']+len(extra))
            error,_=self.rejected(bad);self.assertIn('duplicate',error['message'])
    def test_box_lengths_and_counts_cannot_overflow(self):
        raw,truth,_=self.raw();boxes=truth['boxes']
        for size in (0,1,7,0xffffffff):self.rejected(self.mutate(raw,0,struct.pack('>I',size)))
        for tag,delta in [('stsz',8),('stsc',4),('stco',4),('stts',4),('elst',4),('stsd',4),('dref',4)]:
            for count in (0,0xffffffff):self.rejected(self.mutate(raw,boxes[tag]['data_offset']+delta,struct.pack('>I',count)))
        for suffix in (b'x',struct.pack('>I4sQ',1,b'free',2**64-1),struct.pack('>I4s',0,b'free')):self.rejected(raw+suffix)
    def test_description_track_references_and_encryption_rejected(self):
        raw,truth,_=self.raw();b=truth['boxes'];sd=b['stsd']['data_offset'];tk=b['tkhd']['data_offset'];url=b['dref']['data_offset']+8
        changes=[(sd+12,b'enca'),(sd+24,b'\0\1'),(sd+32,b'\0\x08'),(sd+34,b'\0\x18'),(sd+40,struct.pack('>I',44100<<16)),(sd+22,b'\0\2'),(url+8,bytes(4)),(url+4,b'urn '),(tk+12,bytes(4)),(tk+36,b'\0\x80'),(tk+80,struct.pack('>I',65536)),(b['hdlr']['data_offset']+8,b'vide'),(b['smhd']['data_offset']+4,b'\0\x01')]
        for offset,value in changes:self.rejected(self.mutate(raw,offset,value))
    def test_edit_list_and_duration_strictness(self):
        for version in (0,1):
            raw,truth,_=self.raw(version=version);b=truth['boxes'];el=b['elst']['data_offset'];mv=b['mvhd']['data_offset'];md=b['mdhd']['data_offset'];tk=b['tkhd']['data_offset']
            fields=[(el+4,struct.pack('>I',2)),(el+8+(4 if version==0 else 8),struct.pack('>i' if version==0 else '>q',-1)),(el+(16 if version==0 else 24),struct.pack('>hh',0,0)),(mv+(12 if version==0 else 20),bytes(4)),(md+(12 if version==0 else 20),struct.pack('>I',44100)),(md+(16 if version==0 else 24),struct.pack('>I' if version==0 else '>Q',1)),(tk+(20 if version==0 else 28),struct.pack('>I' if version==0 else '>Q',1))]
            for offset,value in fields:self.rejected(self.mutate(raw,offset,value))
        raw,truth,_=encode(cookie(2),[packet({},2)[0]]*5,remainder=320,movie_timescale=1000)
        p,_,_=self.run_mp4(raw);self.assertEqual(p.returncode,0,p.stderr)
        self.rejected(self.mutate(raw,truth['boxes']['mvhd']['data_offset']+12,struct.pack('>I',1001)))
    def test_index_mapping_zero_sizes_runs_and_overlap(self):
        raw,truth,offsets=self.raw(chunk_pattern=(1,),ctts=True);b=truth['boxes'];stsz=b['stsz']['data_offset'];sc=b['stsc']['data_offset'];co=b['stco']['data_offset'];ts=b['stts']['data_offset'];ct=b['ctts']['data_offset']
        changes=[(stsz+12,0),(stsz+12,16*1024*1024+1),(sc+8,0),(sc+8,2),(sc+12,0),(sc+12,6),(sc+16,2),(co+8,0),(co+12,offsets[0]),(co+8,2**32-1),(ts+8,0),(ts+8,4),(ts+12,1023),(ct+8,4),(ct+12,1)]
        for offset,value in changes:self.rejected(self.mutate(raw,offset,struct.pack('>I',value)))
        raw,truth,_=self.raw(chunk_pattern=(1,2,1));at=truth['boxes']['stsc']['data_offset'];self.rejected(self.mutate(raw,at+20,struct.pack('>I',1)))
    def test_fixed_sizes_and_opaque_unused_packets(self):
        raw,_,_=self.raw(fixed=True);p,_,_=self.run_mp4(raw);self.assertEqual(p.returncode,0,p.stderr)
        for size in (1,127,128,65535,65536,16*1024*1024):
            raw,_,_=encode(cookie(2),[packet({},2)[0],bytes(size)])
            p,_,_=self.run_mp4(raw,'--frames',1);self.assertEqual(p.returncode,0,p.stderr);r=json.loads(p.stdout)
            self.assertEqual(r['packets'],1);self.assertEqual(r['integrity_checked_packets'],2)
        raw,_,_=encode(cookie(2),[packet({},2)[0],bytes(16*1024*1024+1)]);self.rejected(raw,'--frames',1)
    def test_zero_sample_track_and_eof(self):
        raw,_,_=encode(cookie(2),[]);p,out,_=self.run_mp4(raw);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((out/'pcm.f32le').read_bytes(),b'')
        raw,_,_=self.raw();p,out,_=self.run_mp4(raw,'--start-frame',5120,'--frames',1);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual((out/'pcm.f32le').read_bytes(),b'')
        for args in [('--frames',0),('--start-frame',5121),('--start-frame',2**64-1)]:self.rejected(raw,*args)
    def test_output_failure_markers_budget_and_overwrite(self):
        raw,_,_=encode(cookie(2),[packet({},2)[0],b'\xff']);p,out,src=self.run_mp4(raw);self.assertEqual(p.returncode,1);self.assertEqual(json.loads(p.stderr)['error']['packet_index'],1)
        self.assertTrue((out/'.incomplete.json').is_file());before=(out/'pcm.f32le').read_bytes();p=self.run_tool('decode-sq',src,'--out',out);self.assertEqual(p.returncode,1);self.assertEqual((out/'pcm.f32le').read_bytes(),before)
        raw,_,_=encode(cookie(8),[packet({},8)[0]]*40,channels=8);error,out=self.rejected(raw,'--max-output-mib',1);self.assertTrue((out/'.incomplete.json').is_file())
    def test_sequential_warmup_has_no_4096_packet_search_limit(self):
        raw,_,_=encode(cookie(1),[packet({},1)[0]]*4100,channels=1);p,out,_=self.run_mp4(raw,'--start-frame',4097*1024+7,'--frames',13)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout)['warmup_packets'],4097);self.assertEqual((out/'pcm.f32le').read_bytes(),bytes(13*4))
    def test_co64_above_four_gib_uses_sparse_file(self):
        raw,truth,_=self.raw(co64=True);co=truth['boxes']['co64']['data_offset'];mdat=raw.index(b'mdat')-4;delta=2**32
        prefix=bytearray(raw[:mdat]);count=struct.unpack_from('>I',prefix,co+4)[0]
        for i in range(count):struct.pack_into('>Q',prefix,co+8+8*i,struct.unpack_from('>Q',prefix,co+8+8*i)[0]+delta)
        src=self.root/'sparse.mp4'
        with src.open('wb') as f:
            if os.name=='nt':
                import ctypes,msvcrt
                returned=ctypes.c_ulong();fn=ctypes.windll.kernel32.DeviceIoControl;fn.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.c_void_p]
                self.assertTrue(fn(msvcrt.get_osfhandle(f.fileno()),0x900c4,None,0,None,0,ctypes.byref(returned),None))
            f.write(prefix);f.write(struct.pack('>I4sQ',1,b'free',delta));f.seek(mdat+delta);f.write(raw[mdat:])
        p=self.run_tool('decode-sq',src,'--out',self.root/'sparse-out');self.assertEqual(p.returncode,0,p.stderr);self.assertGreater(json.loads(p.stdout)['input']['file_bytes'],2**32)
    def test_manifest_and_validator_reject_missing_or_changed_truth(self):
        from validate_mp4 import MANIFEST,check_input
        self.assertEqual(json.loads(MANIFEST.read_text(encoding='utf-8')),manifest());_,truth,_=self.raw()
        for key in ('packet_count','metadata_sha256','packets_sha256','audio_sha256','layout_source','packet_table','consistency_verified','timeline'):
            bad=dict(truth);bad[key]=None
            with self.assertRaises(AssertionError):check_input(bad,truth)
