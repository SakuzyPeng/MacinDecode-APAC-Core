#!/usr/bin/env python3
"""Fixed-layout native parameters/capacities/off-policy and full corpus input proof."""
import argparse,ctypes as C,hashlib,json,shutil,struct,subprocess
from datetime import datetime,timezone
from pathlib import Path
from channel_vectors import layout,configuration,excitation,packet,cookie,bundle,WINDOWS
from layout_vectors import PROFILE,LAYOUTS,sequences
from validate import require,write_json,caf_chunks,caf_payload,caf_packet_sizes
from validate_channels import check,digest
from validate_channels_native import inspect,refresh_point
from validate_drc import workspace
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_access_native import compare_window
from mp4_native_reference import verify as mp4_verify,AudioFile,PacketTable,Format
from mp4_vectors import encode as mp4_encode
from native_frame_trace import COMPONENT_SHA256

CAPACITIES={12:24576,24:49152}
LABELS={12:[1,2,3,4,5,6,33,34,13,15,52,54],24:[35,36,3,37,33,34,1,2,9,62,55,56,13,15,14,12,52,54,49,51,53,59,57,58]}

def layout_labels():
    lib=C.CDLL('/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox')
    lib.AudioFormatGetPropertyInfo.argtypes=[C.c_uint32,C.c_uint32,C.c_void_p,C.POINTER(C.c_uint32)]
    lib.AudioFormatGetProperty.argtypes=[C.c_uint32,C.c_uint32,C.c_void_p,C.POINTER(C.c_uint32),C.c_void_p]
    rows=[]
    for n in LAYOUTS:
        tag=C.c_uint32((layout(n)[0]<<16)|n);size=C.c_uint32();prop=int.from_bytes(b'cmpl','big')
        require(lib.AudioFormatGetPropertyInfo(prop,4,C.byref(tag),C.byref(size))==0 and size.value==12+20*n,'native layout size differs')
        raw=C.create_string_buffer(size.value);require(lib.AudioFormatGetProperty(prop,4,C.byref(tag),C.byref(size),raw)==0,'native layout property failed')
        labels=[struct.unpack_from('=I',raw.raw,12+20*i)[0] for i in range(n)];require(labels==LABELS[n],'native public label mapping changed')
        rows.append(dict(channels=n,tag=tag.value,labels=labels,property_sha256=hashlib.sha256(raw.raw).hexdigest()))
    return rows

def groups():
    for n in LAYOUTS:
        for rate in (48000,44100):
            options=dict(scene=True,drc=True,rich=True)
            units=[excitation(n,ch,w,gain=100) for ch in range(n) for w in WINDOWS]
            for first in range(0,len(units),4):yield f'unit-{n}-{rate}-{first}',n,rate,options,units[first:first+4],True
            absent=[dict(elements=[None if mask&(1<<i) else {} for i in range(len(layout(n)[2]))]) for mask in [0,(1<<len(layout(n)[2]))-1]+[1<<i for i in range(len(layout(n)[2]))]]
            for first in range(0,len(absent),4):yield f'absence-{n}-{rate}-{first}',n,rate,dict(scene=False,drc=False,rich=False),absent[first:first+4],False
            buckets={}
            for kind,opts,seq in sequences(n):
                if kind in ('joint_tools','metadata_updates','dual_lfe','mixed_element_windows'):buckets.setdefault(kind,[]).append((opts,seq))
            for kind,rows in buckets.items():
                for index in range(0,len(rows),4 if kind=='joint_tools' else 1):
                    opts,seq=rows[index]
                    for first in range(0,len(seq),4):yield f'{kind}-{n}-{rate}-{index}-{first}',n,rate,opts,seq[first:first+4],False

def probe_manifest():
    rows=[]
    for name,n,rate,opts,cases,hard in groups():
        h=hashlib.sha256(cookie(n,rate,**opts))
        for case in cases:
            raw,_=packet(case,n,rate,**opts);h.update(len(raw).to_bytes(8,'little'));h.update(raw)
        rows.append(dict(name=name,channels=n,rate=rate,packets=len(cases),hard_unit=hard,input_sha256=h.hexdigest()))
    controls=[dict(channels=n,rate=rate,profile=profile,signal=signal) for n in LAYOUTS for rate in (48000,44100) for profile in ('default','none','music','speech','movie','capture') for signal in ('noise','impulse')]
    return dict(probes=rows,controls=controls,sha256=digest(dict(probes=rows,controls=controls)))

def artificial(binary,report):
    frozen=probe_manifest();path=Path(__file__).resolve().parents[1]/'data/layout-native-vectors-v1.json'
    require(json.loads(path.read_text(encoding='utf-8'))==frozen,'native input manifest differs');report['native_vector_manifest_sha256']=frozen['sha256'];report['artificial']=[]
    for wanted,(name,n,rate,opts,cases,hard) in zip(frozen['probes'],groups()):
        with workspace(report,name) as root:
            generated=[packet(case,n,rate,**opts) for case in cases];bundle(root/'packets',[p for p,t in generated],n,rate,**opts)
            result,rows,_=inspect(binary,root/'packets',root,len(cases)*1024,hard_unit=hard)
            for r,(_,truth) in zip(rows,generated):check(r,truth,n)
            require(result['capacity_bytes']==CAPACITIES[n],'native capacity changed');result.update(wanted);report['artificial'].append(result)
        print('native layout probe',name,flush=True)
    require(len(report['artificial'])==len(frozen['probes']),'missing native probe')

def controls(binary,report):
    report['controls']=[]
    for spec in probe_manifest()['controls']:
        n,rate,profile,signal=(spec[k] for k in ('channels','rate','profile','signal'));label=f'control-{n}-{rate}-{profile}-{signal}'
        with workspace(report,label) as root:
            extra=[] if profile=='default' else ['--drc-configuration',profile]
            command(binary,'fixture','--out',root/'fixture','--layout','surround714' if n==12 else 'surround222','--sample-rate',rate,'--duration','0.125','--signals',signal,*extra)
            folder=root/'fixture'/signal;actual=json.loads((folder/'manifest.json').read_text())['actual_encoder_settings']['cdrc']
            require(actual['error'] is None and actual['value']=={'default':4294967295,'none':0,'music':1,'speech':2,'movie':3,'capture':4}[profile],'encoder readback differs')
            source=folder/'encoded.caf';directory=root/'packets';command(binary,'dump',source,'--out',directory,'--packets',150)
            m=json.loads((directory/'manifest.json').read_text());table=m['file']['packet_table']['value']
            result,rows,pcm=inspect(binary,directory,root,table['valid_frames']);require(result['capacity_bytes']==CAPACITIES[n],'capacity changed')
            require(all(r['drc_complete'] is (None if profile=='none' else True) for r in rows),'DRC control payload differs')
            data=(directory/'packets.bin').read_bytes();entries=[json.loads(l) for l in (directory/'packets.jsonl').read_text().splitlines()]
            packets=[data[e['export_offset']:e['export_offset']+e['bytes']] for e in entries]
            remux=root/'remux';remux.write_bytes(mp4_encode((directory/'cookie.bin').read_bytes(),packets,rate,n,table['priming_frames'],table['remainder_frames'],len(report['controls']))[0]);evidence=mp4_verify(remux)
            windows=[]
            for kind,start,frames in [('head',0,31),('middle',table['valid_frames']//2,1027),('tail',max(0,table['valid_frames']-1001),2048),('eof',table['valid_frames'],1)]:
                found=[]
                for container,src in [('caf',source),('mp4',remux)]:
                    work=root/(container+'-'+kind);work.mkdir();r=compare_window(binary,src,work,start,frames,evidence if container=='mp4' else None)
                    expected=pcm[start*n*4:(start+r['frames'])*n*4];require(r['pcm_sha256']==hashlib.sha256(expected).hexdigest(),'control range differs from whole PCM');found.append(r);shutil.rmtree(work)
                require(found[0]==found[1],'control CAF/MP4 state differs');windows.append(dict(kind=kind,**found[0]))
            result.update(spec,encoder=actual,windows=windows);report['controls'].append(result)
        print(label,flush=True)
    require(len(report['controls'])==48,'missing encoder controls')

def caf_verify(source):
    chunks=caf_chunks(source);config=caf_payload(source,b'kuki');sizes=caf_packet_sizes(source);count,valid,prime,remainder=struct.unpack('>qqii',caf_payload(source,b'pakt')[:24]);af=AudioFile(source)
    try:
        fmt=af.get('dfmt',Format());native_table=af.get('pnfo',PacketTable());native_count=af.get('pcnt',C.c_uint64()).value
        require(native_count==count==len(sizes) and af.bytes('mgic')==config,'CAF native cookie/count differs')
        table=dict(valid_frames=valid,priming_frames=prime,remainder_frames=remainder)
        require(table==dict(valid_frames=native_table.valid,priming_frames=native_table.priming,remainder_frames=native_table.remainder),'CAF native timeline differs')
        tag,bitmap,descriptions=struct.unpack_from('=III',af.bytes('cmap'));n=fmt.channels
        require(n in LAYOUTS and tag==(layout(n)[0]<<16)|n and bitmap==descriptions==0,'CAF native map differs')
        audio=hashlib.sha256();boundaries=hashlib.sha256();offset=0;seen=0
        with source.open('rb') as f:
            f.seek(chunks[b'data'][0]+4)
            for i,raw in af.packets(count,af.get('psze',C.c_uint32()).value):
                size=sizes[i];require(size==len(raw) and f.read(size)==raw,'CAF native packet bytes differ');audio.update(raw)
                boundaries.update(struct.pack('<QQQQ',i,offset,size,1024));boundaries.update(hashlib.sha256(raw).digest());offset+=size;seen+=1
        require(seen==count and offset==chunks[b'data'][1]-4,'CAF incomplete packet evidence')
        return dict(passed=True,packets=count,channels=n,rate=fmt.rate,layout_tag=tag,cookie_sha256=hashlib.sha256(config).hexdigest(),table=table,audio_sha256=audio.hexdigest(),packets_sha256=boundaries.hexdigest())
    finally:af.close()

def media(binary,report,collection):
    index=json.loads(collection.read_text(encoding='utf-8'));sources={s['source']:s for c in index['configs'] for s in c['sources'] if s['format']['channels'] in LAYOUTS}
    require(len(sources)==126 and sum(s['packet_count']['value'] for s in sources.values())==1698534,'corpus identity/count differs');report['media']=[]
    for path,info in sorted(sources.items()):
        source=Path(path);identity=sha256_file(source);count=info['packet_count']['value'];n=info['format']['channels']
        with source.open('rb') as f:container='caf' if f.read(4)==b'caff' else 'mp4'
        evidence=(caf_verify if container=='caf' else mp4_verify)(source)
        require(evidence['channels']==n and evidence['rate']==info['format']['sample_rate'] and source.stat().st_size==info['file_bytes'],'frozen source format/size changed')
        require(evidence['table']==info['packet_table']['value'] and evidence['packets']==count and evidence['cookie_sha256']==info['cookie']['value']['sha256'],'frozen native metadata changed')
        record=dict(source_sha256=identity,container=container,**evidence,windows=[])
        with workspace(report,identity[:12]+'-refresh') as root:refresh=refresh_point(binary,source,count,root)
        for kind,start in [('head',0),('middle',count//2),('refresh',refresh),('tail',max(0,count-8))]:
            with workspace(report,identity[:12]+'-'+kind) as root:
                command(binary,'dump',source,'--out',root/'packets','--start-packet',start,'--packets',8,'--with-preroll')
                m=json.loads((root/'packets/manifest.json').read_text());bundle_pcm=command(binary,'decode-sq',root/'packets','--out',root/'bundle-pcm','--frames',8192)
                first=bundle_pcm['pcm']['start_frame'];frames=bundle_pcm['saved_frames'];expected=(root/'bundle-pcm/pcm.f32le').read_bytes()
                if kind=='refresh':require(m['start_packet']==start,'refresh requires unqualified dependency')
                work=root/'access';work.mkdir();r=compare_window(binary,source,work,first,frames,evidence if container=='mp4' else None)
                require(r['frames']==frames and r['pcm_sha256']==hashlib.sha256(expected).hexdigest(),'container/access differs from qualified bundle')
                # Native snapshots are bounded to four packets, including a selected
                # current-frame/embedded-frame path, and remain separate from floats.
                command(binary,'dump',source,'--out',root/'native-window','--start-packet',start,'--packets',4,'--with-preroll')
                native_root=root/'native-check';native_root.mkdir();native,_,_=inspect(binary,root/'native-window',native_root,4096)
                r.update(kind=kind,target_packet=start,independent_start_packet=m['start_packet'],native=native);record['windows'].append(r)
            print('layout media',len(report['media']),n,kind,flush=True)
        require(len(record['windows'])==4 and all(w['passed'] for w in record['windows']),'incomplete media windows')
        require(sha256_file(source)==identity,'media changed');report['media'].append(record)
    require(len(report['media'])==126 and sum(r['packets'] for r in report['media'])==1698534,'missing corpus evidence')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--collection',type=Path,required=True)
    a=p.parse_args();require(a.binary.is_file() and not a.report.exists(),'binary missing or report exists')
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs');require(sha256_file(component)==COMPONENT_SHA256,'native component changed')
    r=dict(schema_version=1,passed=False,profile=PROFILE,code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),component_sha256=COMPONENT_SHA256,collection_sha256=sha256_file(a.collection),
           created_at=datetime.now(timezone.utc).isoformat(),errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        r['public_layouts']=layout_labels();artificial(a.binary,r);controls(a.binary,r);media(a.binary,r,a.collection)
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'] and sha256_file(component)==COMPONENT_SHA256 and sha256_file(a.collection)==r['collection_sha256'],'source/binary/component/collection changed during proof');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],errors=r['errors'])));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
