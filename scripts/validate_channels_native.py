#!/usr/bin/env python3
"""Hash-qualified channel boundaries/mapping and DRC-off proof; float drift is diagnostic."""
import argparse,hashlib,json,struct,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
from channel_vectors import LAYOUTS,WINDOWS,configuration,excitation,packet,bundle
from native_channels_trace import trace_bundle
from native_drc_trace import trace_bundle as drc_trace
from validate_drc_off import inspect as off_proof
from validate_drc_native import native_parameters
from native_frame_trace import COMPONENT_SHA256
from validate_channels import compare,merge_metrics,digest,check
from validate_portable import source_digest
from validate_replay import command,sha256_file
from validate_drc import workspace
from validate import require,write_json
from packet_vectors import window


def empty_metrics():return dict(max_absolute_error=0.,max_ulp=0,failed_samples=0,first_failure=None)
def native_check(reports,trace):
    events=trace.get('packet_events',trace['events']);expected={};boundaries=[];metrics={k:empty_metrics() for k in ('raw','after_cac','after_tns','after_bwe2')}
    def visit(r,sequence,role):
        if r['embedded_preroll']:visit(r['embedded_preroll']['report'],sequence,'preroll')
        expected[sequence,role]=r
    for i,r in enumerate(reports):visit(r,i,'current')
    require(len(trace['packets'])==len(reports),'native packet count differs')
    for (sequence,role),r in expected.items():
        own=[e for e in events if e['sequence']==sequence and e['role']==role]
        cores=[e for e in own if e['kind']=='core'];require(len(cores)==1,'missing/duplicate native core')
        core=cores[0];require(core['status']==0 and core['start']['buffer_sha256']==r['packet_sha256'],'native core failed or input differs')
        require(core['end']['relative_bit_offset']==r['component_end_bit_offset'],'core alignment endpoint differs')
        require(core['elements']==[dict(element_index=e['configuration']['element_index'],element_type=e['configuration']['tce_type'],channels=e['configuration']['output_channels']) for e in r['elements']],'native element/output map differs')
        for e in r['elements']:
            index=e['configuration']['element_index'];parts=[x for x in own if x.get('element_index')==index];kind='element' if e['present'] else 'reset'
            entries=[x for x in parts if x['kind']==kind];require(len(entries)==1,'missing native element return')
            native=entries[0]
            if e['present']:
                require(native['status']==0 and native['start']['relative_bit_offset']==e['start_bit_offset']+1 and native['end']['relative_bit_offset']==e['end_bit_offset'],'element boundary differs')
                for c in e['channels']:
                    local=c['channel_index'];global_index=e['configuration']['output_channels'][local]
                    streams=[x for x in parts if x['kind']=='stream' and x['channel_index']==global_index];require(len(streams)==1,'missing/duplicate stream')
                    x=streams[0]
                    require(x['status']==0 and x['start']['relative_bit_offset']==c['stream_bit_offset'] and x['end']['relative_bit_offset']==c['end_bit_offset'],'stream endpoint differs')
                    require(x['ics']['block_type']==c['ics']['block_type'] and x['ics']['max_sfb']==c['ics']['max_sfb'],'native ICS differs')
                    groups=c['ics']['window_groups'] if c['ics']['max_sfb'] else []
                    require(x['ics']['active_window_groups']==groups,'native window grouping differs')
                    merge_metrics(metrics['raw'],compare(c['scaled'],x['scaled'],dict(sequence=sequence,role=role,channel=global_index,stage='raw')))
                    merge_metrics(metrics['after_tns'],compare(e['channels_after_tns'][local]['scaled'],native['after'][local]['scaled'],dict(sequence=sequence,role=role,channel=global_index,stage='after_tns')))
                tns=[x for x in parts if x['kind']=='tns']
                for local,data in enumerate(e['tns']):
                    found=[x for x in tns if x['channel_index']==e['configuration']['output_channels'][local]]
                    if not data['present']:require(not found,'native parsed absent TNS')
                    else:
                        require(len(found)==1,'missing native TNS');x=found[0]
                        require(x['status']==0 and x['start']['relative_bit_offset']==data['start_bit_offset']+1 and x['end']['relative_bit_offset']==data['end_bit_offset'],'TNS bit range differs')
                        require(len(x['parameters']['windows'])==len(data['windows']),'TNS window count differs')
                        for actual,wanted in zip(x['parameters']['windows'],data['windows']):
                            require(len(actual['filters'])==len(wanted['filters']),'TNS filter count differs')
                            for a,b in zip(actual['filters'],wanted['filters']):
                                for key in ('length','order','direction','quantized'):require(a[key]==b[key],'native TNS '+key+' differs')
                                if b['order']:require(a['resolution']==wanted['resolution'],'TNS resolution differs')
                cac=[x for x in parts if x['kind']=='cac']
                if e['shared_ics']:
                    require(len(cac)==1,'missing native CAC');x=cac[0]
                    require(x['position']['relative_bit_offset']==e['cac']['end_bit_offset'],'CAC return boundary differs')
                    require(x['runs']==[{k:v for k,v in run.items() if k in ('gain_index','repeat_code')} for run in e['cac']['runs']],'CAC runs differ')
                    for local,values in enumerate(x['after']):merge_metrics(metrics['after_cac'],compare(e['channels_after_cac'][local]['scaled'],values,dict(sequence=sequence,role=role,channel=e['configuration']['output_channels'][local],stage='after_cac')))
                else:require(not cac,'unexpected native CAC')
            bwe=[x for x in parts if x['kind']=='bwe2']
            if not e['present']:require(not bwe,'absent element consumed BWE2')
            else:
                require(len(bwe)==1,'missing native BWE2 dispatch');x=bwe[0];require(x['status']==0,'native BWE2 parse failed')
                require(x['channel_start']==e['configuration']['output_channels'][0],'BWE2 global channel start differs')
                if e['bwe2'] is None:require(x['start']['relative_bit_offset']==x['end']['relative_bit_offset']==e['end_bit_offset'],'LFE consumed tool bits')
                else:
                    data=e['bwe2'];require((x['start']['relative_bit_offset'],x['end']['relative_bit_offset'])==(data['start_bit_offset'],data['end_bit_offset']),'BWE2 bit range differs')
                    for local,(actual,wanted) in enumerate(zip(x['parameters'],data['channels'])):
                        require(actual['active']==wanted['active'],'BWE2 active state differs')
                        if wanted['active']:
                            p=wanted['parameters'];require(actual['lsf_indices']==p['lsf_indices'],'LSF indices differ')
                            groups=len(e['channels'][local]['ics']['window_groups']) if e['channels'][local]['ics']['max_sfb'] else 0
                            require(actual['gain_indices']==p['gain_indices'][:groups],'BWE2 gains/reuse differ')
        synth=[x for x in own if x['kind']=='synthesis'];require([e['channel_index'] for e in synth]==list(range(r['channel_count'])),'native synthesis channel order differs')
        for e in r['elements']:
            if e['present']:
                for local,global_index in enumerate(e['configuration']['output_channels']):
                    merge_metrics(metrics['after_bwe2'],compare(e['channels_after_bwe2'][local]['scaled'],synth[global_index]['input'],dict(sequence=sequence,role=role,channel=global_index,stage='after_bwe2')))
        ancillary=[e for e in own if e['kind']=='ancillary'];require(len(ancillary)==1 and ancillary[0]['status']==0,'missing ancillary result')
        tail=r['packet_tail'];require(ancillary[0]['start']['relative_bit_offset']==tail['ancillary_start_bit_offset'] and ancillary[0]['end']['relative_bit_offset']==tail['ancillary_end_bit_offset'],'ancillary bit range differs')
        boundaries.append(dict(sequence=sequence,role=role,sha256=r['packet_sha256'],elements=[dict(configuration=e['configuration'],present=e['present'],start=e['start_bit_offset'],end=e['end_bit_offset'],bwe2=e['bwe2']) for e in r['elements']],tail=tail))
    capacities=[e['preroll_bytes'] for e in events if e['kind']=='capacity'];require(capacities and set(capacities)=={{1:2048,2:4096,6:12288,8:16384,12:24576,16:32768,24:49152}[reports[0]['channel_count']]},'native preroll capacity differs')
    return dict(passed=True,frames=len(expected),boundary_sha256=digest(boundaries),capacity_bytes=capacities[0],float_metrics=metrics)


def inspect(binary,directory,root,frames,hard_unit=False):
    m=json.loads((directory/'manifest.json').read_text());count=m['actual_packets'];n=m['file']['format']['channels']
    parsed=command(binary,'parse-packets',directory,'--depth','channels','--packets',count,'--output',root/'parsed.jsonl')
    require(parsed['channel_packets_complete']==count and parsed['errors']==0,'incomplete Rust channel parse')
    rows=[json.loads(l)['report'] for l in (root/'parsed.jsonl').read_text().splitlines()]
    drc_present=rows[0]['drc_complete'] is not None
    native=root/'native';native.mkdir()
    trace=drc_trace(binary,directory,native,frames,channel_mode=True) if drc_present else trace_bundle(binary,directory,native,frames)
    result=native_check(rows,trace)
    out=native/'native-pcm';replay=json.loads((out/'replay.json').read_text());meta=json.loads((out/'pcm.json').read_text());raw=(out/'pcm.f32le').read_bytes()
    if drc_present:
        result['off_proof']=off_proof(trace,replay,meta,raw,'drc-off',require_nonzero=hard_unit)
        require(result['off_proof']['passed'],'DRC off kernel/selection/timing failed')
        result['drc_parameters']=native_parameters(rows,trace)
    decoded=command(binary,'decode-sq',directory,'--out',root/'rust-pcm','--frames',frames)
    pcm=(root/'rust-pcm/pcm.f32le').read_bytes();require(len(pcm)==len(raw) and decoded['pcm']['start_frame']==meta['start_frame'],'native output timeline differs')
    result['pcm_metrics']=compare(struct.unpack('<'+str(len(pcm)//4)+'f',pcm),struct.unpack('<'+str(len(raw)//4)+'f',raw),dict(channels=n,stage='native_pcm'))
    result.update(pcm_sha256=sha256_file(root/'rust-pcm/pcm.f32le'),frames_saved=decoded['saved_frames'],packets=count,channels=n)
    if hard_unit:
        require(all(v['failed_samples']==0 for v in result['float_metrics'].values()),'unit/boundary native spectrum exceeds tolerance')
        require(result['pcm_metrics']['passed'],'unit/boundary native PCM exceeds tolerance')
    return result,rows,pcm


def artificial(binary,report):
    from channel_native_vectors import groups,manifest
    expected=manifest();frozen=json.loads((Path(__file__).resolve().parents[1]/'data/channel-native-vectors-v1.json').read_text())
    require(expected==frozen,'native probe manifest differs');report['native_vector_manifest_sha256']=frozen['sha256'];report['artificial']=[]
    for required,(label,n,rate,options,cases,hard) in zip(frozen['probes'],groups()):
        with workspace(report,label) as root:
            generated=[packet(c,n,rate,**options) for c in cases];bundle(root/'packets',[p for p,_ in generated],n,rate,**options)
            result,rows,_=inspect(binary,root/'packets',root,len(cases)*1024,hard_unit=hard)
            for r,(_,truth) in zip(rows,generated):check(r,truth,n)
            result.update(required);report['artificial'].append(result)
        print(label,flush=True)
    require(len(report['artificial'])==len(frozen['probes']),'missing native probe')


def controls(binary,report):
    report['controls']=[]
    for n,preset in ((1,'mono'),(6,'surround51'),(8,'surround71')):
        for rate in (48000,44100):
            for profile in ('default','none','music','speech','movie','capture'):
                for signal in ('noise','impulse'):
                    label=f'control-{n}-{rate}-{profile}-{signal}'
                    with workspace(report,label) as root:
                        args=[] if profile=='default' else ['--drc-configuration',profile]
                        command(binary,'fixture','--out',root/'fixture','--layout',preset,'--sample-rate',rate,'--duration','0.125','--signals',signal,*args)
                        src=root/'fixture'/signal;fixture=json.loads((src/'manifest.json').read_text());actual=fixture['actual_encoder_settings']['cdrc']
                        require(actual['error'] is None and actual['value']=={'default':4294967295,'none':0,'music':1,'speech':2,'movie':3,'capture':4}[profile],'encoder control readback differs')
                        command(binary,'dump',src/'encoded.caf','--out',root/'packets','--packets',150)
                        m=json.loads((root/'packets/manifest.json').read_text());frames=m['file']['packet_table']['value']['valid_frames']
                        result,rows,pcm=inspect(binary,root/'packets',root,frames)
                        result.update(rate=rate,profile=profile,signal=signal,encoder=actual)
                        if profile=='none':require(all(r['drc_complete'] is None for r in rows),'None encoding still carries DRC')
                        else:require(all(r['drc_complete'] is True for r in rows),'default/explicit DRC absent')
                        decoded=command(binary,'decode-sq',src/'encoded.caf','--out',root/'caf-pcm');require((root/'caf-pcm/pcm.f32le').read_bytes()==pcm and decoded['saved_frames']==frames,'CAF/bundle media PCM differs')
                        result['ranges']=[]
                        for name,start in [('head',0),('middle',frames//2),('tail',max(0,frames-1001))]:
                            out=root/name;r=command(binary,'decode-sq',src/'encoded.caf','--out',out,'--start-frame',start,'--frames',1027);raw=(out/'pcm.f32le').read_bytes()
                            require(raw==pcm[start*n*4:(start+r['saved_frames'])*n*4],'CAF range differs from continuous PCM')
                            result['ranges'].append(dict(kind=name,frames=r['saved_frames'],sha256=hashlib.sha256(raw).hexdigest()))
                        report['controls'].append(result)
                    print(label,flush=True)
    require(len(report['controls'])==72,'missing encoder controls')


def refresh_point(binary,source,count,root):
    import shutil
    begin=count//2
    for batch in range(8):
        start=begin+128*batch
        if start>=count:break
        directory=root/('search-'+str(batch))
        command(binary,'dump',source,'--out',directory,'--start-packet',start,'--packets',min(128,count-start),'--with-preroll')
        data=(directory/'packets.bin').read_bytes()
        rows=[json.loads(l) for l in (directory/'packets.jsonl').read_text().splitlines()]
        found=[]
        for row in rows:
            dependency=row['dependency']['value']
            if row['packet_index']>=start and data[row['export_offset']]>>3==17 and dependency and dependency['independently_decodable'] and dependency['preroll_packet_count']==0 and row['roll_distance']['value']==0:
                found.append(row['packet_index'])
        shutil.rmtree(directory)
        if found:return found[0]
    raise AssertionError('no independently qualified embedded-preroll refresh point in bounded search')


def media(binary,report,collection):
    index=json.loads(collection.read_text());sources={s['source']:s for c in index['configs'] for s in c['sources'] if s['format']['channels']==8}
    require(len(sources)==13,'expected 13 frozen 7.1 sources');report['media']=[]
    for source,info in sorted(sources.items()):
        count=info['packet_count']['value'];record=dict(source_sha256=sha256_file(Path(source)),channels=8,windows=[])
        with workspace(report,record['source_sha256'][:12]+'-refresh-search') as root:refresh=refresh_point(binary,source,count,root)
        for name,start in [('head',0),('middle',count//2),('refresh',refresh),('tail',max(0,count-8))]:
            with workspace(report,record['source_sha256'][:12]+'-'+name) as root:
                command(binary,'dump',source,'--out',root/'packets','--start-packet',start,'--packets',8,'--with-preroll')
                result,_,pcm=inspect(binary,root/'packets',root,8192)
                m=json.loads((root/'packets/manifest.json').read_text());target=m['replay_window']['requested_start_packet']
                if name=='refresh':require(m['start_packet']==target,'refresh requires previous packets')
                # Longer continuous reference starts at a separately qualified
                # access point; it is not claimed to start at stream origin.
                long_start=max(0,start-32);command(binary,'dump',source,'--out',root/'long-packets','--start-packet',long_start,'--packets',start-long_start+8,'--with-preroll')
                continuous=command(binary,'decode-sq',root/'long-packets','--out',root/'continuous')
                selected=json.loads((root/'rust-pcm/decode-sq.json').read_text())
                first=selected['pcm']['start_frame']-continuous['pcm']['start_frame'];full=(root/'continuous/pcm.f32le').read_bytes()
                require(first>=0 and full[first*32:first*32+len(pcm)]==pcm,'qualified random window differs from longer continuous decode')
                result.update(kind=name,target_packet=target,independent_start_packet=m['start_packet'],continuous_start_frame=continuous['pcm']['start_frame'],continuous_overlap_exact=True,rate=info['format']['sample_rate']);record['windows'].append(result)
            print('7.1 media',len(report['media']),name,flush=True)
        require(sha256_file(Path(source))==record['source_sha256'],'source media changed')
        record['passed']=all(r['passed'] for r in record['windows']);report['media'].append(record)
    require(len(report['media'])==13 and all(r['passed'] for r in report['media']),'missing or failed 7.1 source')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--report',type=Path,required=True);p.add_argument('--collection',type=Path,default=Path('artifacts/configs-v1/index.json'))
    args=p.parse_args();require(args.binary.is_file() and not args.report.exists(),'binary missing or report exists')
    report=dict(schema_version=1,passed=False,created_at=datetime.now(timezone.utc).isoformat(),code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                source_sha256=source_digest(),binary_sha256=sha256_file(args.binary),component_sha256=COMPONENT_SHA256,errors=[],failure_directory=str(args.report.with_suffix('.failures')))
    try:
        artificial(args.binary,report);controls(args.binary,report);media(args.binary,report,args.collection)
        require(source_digest()==report['source_sha256'] and sha256_file(args.binary)==report['binary_sha256'],'source or binary changed during native acceptance')
        report['passed']=True
    except Exception as e:report['errors'].append(str(e))
    write_json(args.report,report);print(json.dumps(dict(passed=report['passed'],errors=report['errors'])));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
