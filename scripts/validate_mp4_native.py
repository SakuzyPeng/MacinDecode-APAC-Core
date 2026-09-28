#!/usr/bin/env python3
"""Native container identity and exact origin-warmed MP4/bundle media windows."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
from datetime import datetime,timezone
from mp4_native_reference import verify
from mp4_vectors import PROFILE,encode,sha
from validate import caf_payload,caf_packet_sizes,require,write_json
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import command,sha256_file
from native_frame_trace import COMPONENT_SHA256

def check_input(report,evidence):
    r=report['input'];require(r['kind']=='mp4' and r['profile']==PROFILE and r['consistency_verified'],'unverified MP4 input')
    for key in ('audio_sha256','packets_sha256','cookie_sha256'):require(r[key]==evidence[key],'native/Rust container identity differs: '+key)
    require(r['packet_table']==evidence['table'] and r['packet_count']==evidence['packets'],'native/Rust timeline differs')
    require(report['pcm']['sample_rate']==evidence['rate'],'native/Rust sample rate differs')
    require(report['pcm']['channels']==evidence['channels'] and report['pcm']['layout']['value']['tag']==evidence['layout_tag'],'native/Rust channel map differs')
    require(report['integrity_checked_packets']==evidence['packets'],'unused packets not verified')
    require(report['drc_processing']==report['loudness_normalization']=='off','unexpected playback processing')
    require(report['complete'] and report['experimental'] and not report['native_apis_used'],'wrong completion/scope')

def same_pcm(a,b,frames,channels):
    require(len(a)==frames*channels*4 and a==b,'MP4/bundle/CAF PCM differs')

def controls(binary,report):
    report['controls']=[]
    for n,preset in ((1,'mono'),(2,'stereo'),(6,'surround51'),(8,'surround71')):
        for rate in (48000,44100):
            for profile in ('default','none','music','speech','movie','capture'):
                for signal in ('noise','impulse'):
                    label=f'control-{n}-{rate}-{profile}-{signal}'
                    with workspace(report,label) as root:
                        args=[] if profile=='default' else ['--drc-configuration',profile]
                        command(binary,'fixture','--out',root/'fixture','--layout',preset,'--sample-rate',rate,'--duration','0.125','--signals',signal,*args)
                        src=root/'fixture'/signal;fixture=json.loads((src/'manifest.json').read_text(encoding='utf-8'));actual=fixture['actual_encoder_settings']['cdrc']
                        require(actual['error'] is None and actual['value']=={'default':4294967295,'none':0,'music':1,'speech':2,'movie':3,'capture':4}[profile],'encoder setting differs')
                        caf=src/'encoded.caf';config=caf_payload(caf,b'kuki');sizes=caf_packet_sizes(caf);audio=caf_payload(caf,b'data')[4:];packets=[];offset=0
                        for size in sizes:packets.append(audio[offset:offset+size]);offset+=size
                        require(offset==len(audio),'CAF source bounds')
                        native_caf=command(binary,'inspect',caf);table=native_caf['packet_table']['value']
                        mp4=root/'remux.m4a';raw,truth,_=encode(config,packets,rate,n,table['priming_frames'],table['remainder_frames'],len(report['controls']));mp4.write_bytes(raw)
                        evidence=verify(mp4);require(evidence['table']==table and evidence['channels']==n,'native remux changed format/timing')
                        command(binary,'dump',mp4,'--out',root/'bundle','--packets',len(packets))
                        require((root/'bundle/cookie.bin').read_bytes()==config and (root/'bundle/packets.bin').read_bytes()==audio,'native dump disagrees with public read proof')
                        valid=table['valid_frames'];full=None;windows=[]
                        for name,start,count in [('all',0,valid),('head',0,31),('middle',valid//2,1027),('tail',max(0,valid-1001),2048)]:
                            outputs=[]
                            for kind,source in [('mp4',mp4),('caf',caf),('bundle',root/'bundle')]:
                                out=root/(kind+'-'+name);r=command(binary,'decode-sq',source,'--out',out,'--start-frame',start,'--frames',count)
                                if kind=='mp4':check_input(r,evidence)
                                pcm=(out/'pcm.f32le').read_bytes();require(sha(pcm)==r['pcm']['sha256'],'PCM file/hash differs');outputs.append(pcm)
                                shutil.rmtree(out)
                            frames=min(count,valid-start);same_pcm(outputs[0],outputs[1],frames,n);same_pcm(outputs[0],outputs[2],frames,n)
                            if name=='all':full=outputs[0]
                            same_pcm(outputs[0],full[start*4*n:(start+frames)*4*n],frames,n)
                            windows.append(dict(kind=name,start=start,frames=frames,pcm_sha256=sha(outputs[0]),passed=True))
                        evidence.update(profile=profile,signal=signal,encoder=actual,source_sha256=sha(raw),windows=windows);report['controls'].append(evidence)
                    print(label,flush=True)
    require(len(report['controls'])==96,'missing encoder control')

def media(binary,report,collection,prior):
    index=json.loads(collection.read_text(encoding='utf-8'));sources={s['source']:s for c in index['configs'] for s in c['sources'] if s['format']['channels']==8}
    require(len(sources)==13,'expected 13 frozen 7.1 sources');baseline=json.loads(prior.read_text(encoding='utf-8'))
    require(baseline['passed'] and len(baseline['media'])==13,'missing prior qualified native media')
    old={r['source_sha256']:r for r in baseline['media']};report['media']=[]
    for path,info in sorted(sources.items()):
        source=Path(path);identity=sha256_file(source);require(identity in old and old[identity]['passed'],'original source no longer matches prior qualification')
        evidence=verify(source);evidence.update(source_sha256=identity,windows=[])
        require(evidence['table']==info['packet_table']['value'],'frozen packet table changed')
        for before in old[identity]['windows']:
            name=before['kind'];start=before['target_packet']
            with workspace(report,identity[:12]+'-'+name) as root:
                command(binary,'dump',source,'--out',root/'bundle','--start-packet',start,'--packets',8,'--with-preroll')
                selected=command(binary,'decode-sq',root/'bundle','--out',root/'reference','--frames',8192)
                offset=selected['pcm']['start_frame'];frames=selected['saved_frames'];expected=(root/'reference/pcm.f32le').read_bytes()
                require(selected['pcm']['sha256']==before['pcm_sha256'] and frames==before['frames_saved'],'previous qualified window PCM changed')
                decoded=command(binary,'decode-sq',source,'--out',root/'mp4','--start-frame',offset,'--frames',frames)
                check_input(decoded,evidence);actual=(root/'mp4/pcm.f32le').read_bytes();same_pcm(actual,expected,frames,8)
                require(decoded['warmup_packets']==(offset+evidence['table']['priming_frames'])//1024,'did not warm from packet zero')
                if name=='tail':require(decoded['packets']==evidence['packets'],'tail did not decode complete source history')
                evidence['windows'].append(dict(kind=name,start=offset,frames=frames,pcm_sha256=sha(actual),warmup_packets=decoded['warmup_packets'],decoded_packets=decoded['packets'],passed=True))
            print('M4A',len(report['media']),name,flush=True)
        require(sha256_file(source)==identity,'source changed during native validation');report['media'].append(evidence)
    require(len(report['media'])==13 and sum(r['packets'] for r in report['media'])==207879,'original packet corpus incomplete')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--collection',type=Path,required=True);p.add_argument('--channel-reference',type=Path,required=True)
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    component=Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
    report=dict(schema_version=1,passed=False,profile=PROFILE,created_at=datetime.now(timezone.utc).isoformat(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),component_sha256=sha256_file(component),channel_reference_sha256=sha256_file(args.channel_reference),
                controls=[],media=[],errors=[],failure_directory=str(args.report.with_suffix('.failures')))
    try:
        require(report['component_sha256']==COMPONENT_SHA256,'unqualified native component')
        controls(args.binary.resolve(),report);media(args.binary.resolve(),report,args.collection,args.channel_reference)
        require(len(report['controls'])==96 and len(report['media'])==13,'missing native acceptance groups')
        require(sha256_file(args.binary)==report['binary_sha256'] and source_digest()==report['source_sha256'] and sha256_file(component)==COMPONENT_SHA256,'validation identity changed');report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    write_json(args.report,report);print(json.dumps(dict(passed=report['passed'],controls=len(report['controls']),media=len(report['media']),errors=report['errors'])))
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
