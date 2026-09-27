#!/usr/bin/env python3
"""AudioFile CAF container proof and exact Rust CAF/bundle media closure."""
import argparse,hashlib,json,struct,subprocess
from pathlib import Path
from datetime import datetime,timezone
from caf_vectors import PROFILE,sha
from validate import caf_chunks,caf_payload,caf_packet_sizes,require,write_json
from validate_replay import command,sha256_file
from validate_portable import source_digest
from validate_drc import workspace
from native_frame_trace import COMPONENT_SHA256

def inspect(binary,source,root):
    source_hash=sha256_file(source);native=command(binary,'inspect',source)
    chunks=caf_chunks(source);sizes=caf_packet_sizes(source);config=caf_payload(source,b'kuki')
    count=native['packet_count']['value'];table=native['packet_table']['value']
    require(count==len(sizes) and native['cookie']['value']==dict(bytes=len(config),sha256=sha(config)),'native cookie/count differs from CAF wire')
    packet_header=struct.unpack('>qqii',caf_payload(source,b'pakt')[:24])
    require(packet_header==(count,table['valid_frames'],table['priming_frames'],table['remainder_frames']),'native packet table differs from CAF wire')
    command(binary,'dump',source,'--out',root/'packets','--start-packet',0,'--packets',count)
    rows=[json.loads(l) for l in (root/'packets/packets.jsonl').read_text(encoding='utf-8').splitlines()]
    require(len(rows)==count and (root/'packets/cookie.bin').read_bytes()==config,'native export omitted packets/cookie')
    digest=hashlib.sha256();audio=hashlib.sha256();offset=0
    with source.open('rb') as f,(root/'packets/packets.bin').open('rb') as exported:
        f.seek(chunks[b'data'][0]+4)
        for i,(size,row) in enumerate(zip(sizes,rows)):
            raw=f.read(size);require(len(raw)==size and raw==exported.read(size),'native packet bytes differ')
            require(row['packet_index']==i and row['raw_frame_position']['value']==i*1024 and row['frames']==1024 and row['bytes']==size,'native packet boundaries/timeline differ')
            require(row['sha256']==sha(raw),'native packet digest differs')
            digest.update(struct.pack('<QQQQ',i,offset,size,1024));digest.update(hashlib.sha256(raw).digest());audio.update(raw);offset+=size
        require(not exported.read(1) and offset==chunks[b'data'][1]-4,'native export does not cover data chunk')
    full=None;windows=[];valid=table['valid_frames'];prime=table['priming_frames']
    refresh=next((i*1024-prime for i,r in enumerate(rows) if i*1024>prime and (r['dependency']['value'] or {}).get('independently_decodable')),valid//2)
    ranges=[('all',0,valid),('head',0,8192),('middle',valid//2,8192),('refresh',min(refresh,valid),8192),('tail',max(0,valid-8192),8192)]
    for name,start,frames in ranges:
        results=[]
        for kind,src in [('caf',source),('bundle',root/'packets')]:
            out=root/(kind+'-'+name);r=command(binary,'decode-sq',src,'--out',out,'--start-frame',start,'--frames',frames)
            raw=(out/'pcm.f32le').read_bytes();require(r['complete'] and sha(raw)==r['pcm']['sha256'],'incomplete PCM')
            if kind=='caf':
                require(r['input']['profile']==PROFILE and r['input']['consistency_verified'],'input not qualified')
                require(r['input']['packets_sha256']==digest.hexdigest() and r['input']['audio_sha256']==audio.hexdigest(),'Rust CAF packet identity differs from native/raw bytes')
                require(r['input']['cookie_sha256']==sha(config) and r['input']['packet_table']==table,'Rust CAF cookie/timing differs')
            results.append(raw)
        require(results[0]==results[1],'Rust CAF/bundle PCM differs')
        if name=='all':full=results[0]
        require(results[0]==full[start*8:min(valid,start+frames)*8],'range differs from sequential PCM')
        windows.append(dict(kind=name,start=start,frames=len(results[0])//8,sha256=sha(results[0]),passed=True))
        # Keep only the full PCM bytes in memory; each exported window is disposable.
        import shutil
        for kind in ('caf','bundle'):shutil.rmtree(root/(kind+'-'+name))
    require(sha256_file(source)==source_hash,'CAF changed during native proof')
    return dict(passed=True,source_sha256=source_hash,source_bytes=source.stat().st_size,rate=native['format']['sample_rate'],packets=count,
                cookie_sha256=sha(config),packets_sha256=digest.hexdigest(),audio_sha256=audio.hexdigest(),table=table,windows=windows)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--caf',type=Path,action='append',required=True)
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
    report=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),
                code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),
                component_sha256=sha256_file(component),controls=[],media=[],errors=[],failure_directory=str(args.report.with_suffix('.failures')))
    try:
        require(report['component_sha256']==COMPONENT_SHA256,'unqualified native component')
        for rate in (48000,44100):
            for profile in ('default','music','speech','movie','capture'):
                for signal in ('noise','impulse'):
                    label=f'{rate}-{profile}-{signal}'
                    with workspace(report,label) as root:
                        extra=[] if profile=='default' else ['--drc-configuration',profile]
                        command(args.binary,'fixture','--out',root/'fixture','--sample-rate',rate,'--duration','0.125','--signals',signal,*extra)
                        src=root/'fixture'/signal;manifest=json.loads((src/'manifest.json').read_text(encoding='utf-8'))
                        actual=manifest['actual_encoder_settings']['cdrc']
                        require(actual['error'] is None and actual['value']=={'default':4294967295,'music':1,'speech':2,'movie':3,'capture':4}[profile],'encoder readback differs')
                        record=inspect(args.binary,src/'encoded.caf',root);record.update(profile=profile,signal=signal,encoder=actual);report['controls'].append(record)
                    print('CAF native',label,flush=True)
        for i,source in enumerate(args.caf):
            with workspace(report,'media-'+str(i)) as root:report['media'].append(inspect(args.binary,source,root))
        require(len(report['controls'])==20 and len(report['media'])==len(args.caf),'missing native case')
        require(sha256_file(args.binary)==report['binary_sha256'] and source_digest()==report['source_sha256'] and sha256_file(component)==COMPONENT_SHA256,'proof input changed')
        report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    write_json(args.report,report);print(json.dumps(dict(passed=report['passed'],controls=len(report['controls']),media=len(report['media']),errors=report['errors'])))
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
