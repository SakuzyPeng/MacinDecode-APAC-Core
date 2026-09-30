#!/usr/bin/env python3
"""Two representative native encoding controls for the HOA order/rate extension."""
import argparse,json,subprocess
from pathlib import Path
from validate import require,write_json
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import command,sha256_file


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True);p.add_argument('--hoa1-control',type=Path,required=True);p.add_argument('--hoa2-control',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'missing binary or report exists')
    r=dict(passed=False,profile='apac-hoa-orders-controls-v1',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),controls=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for root,order,rate,drc in [(a.hoa1_control,1,44100,True),(a.hoa2_control,2,48000,False)]:
            manifest=root/'channel-solo/manifest.json';m=json.loads(manifest.read_text());source=root/'channel-solo/encoded.caf';n=(order+1)**2;frames=rate*2
            require(m['complete'] and m['signal']=='channel-solo' and m['requested']['layout']=='hoa'+str(order) and m['requested']['sample_rate']==rate and m['requested']['frames']==frames,'unexpected representative control')
            if drc:require(m['requested']['drc_configuration'] is None,'first-order control must preserve encoder default')
            else:require(m['requested']['drc_configuration']=='none' and m['drc_configuration_verified'] is True and m['actual_encoder_settings']['cdrc']['value']==0,'second-order DRC-none request not verified')
            fingerprint=sha256_file(source)
            with workspace(r,'order'+str(order)) as temporary:
                decoded=command(a.binary,'decode-sq',source,'--out',temporary/'pcm');pcm=temporary/'pcm/pcm.f32le';impl=decoded['pcm']['decoder_settings']['implementation']['value']
                require(decoded['saved_frames']==frames and decoded['pcm']['channels']==n and decoded['pcm']['sample_rate']==rate and decoded['pcm']['layout']['value']['ambisonic_order']==order,'wrong output shape/timeline')
                require(decoded['input']['consistency_verified'] and decoded['drc_payloads_complete'] and decoded['drc_processing']==decoded['loudness_normalization']=='off','incomplete verification or wrong processing policy')
                require((decoded['drc_payload_frames']>0)==drc,'DRC class differs');require(pcm.stat().st_size==frames*n*4,'PCM byte count differs');require(sha256_file(source)==fingerprint,'control changed')
                r['controls'].append(dict(order=order,rate=rate,channels=n,frames=frames,packets=decoded['packets'],embedded_frames=decoded['embedded_preroll_frames'],drc_payload_frames=decoded['drc_payload_frames'],pcm_sha256=sha256_file(pcm),source_sha256=fingerprint,manifest_sha256=sha256_file(manifest),encoder_settings=m['actual_encoder_settings'],implementation=impl,input=decoded['input']))
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'source/binary changed');r['passed']=True
    except Exception as error:r['errors'].append(str(error))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],controls=len(r['controls']),errors=r['errors'])));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
