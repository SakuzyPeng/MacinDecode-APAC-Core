#!/usr/bin/env python3
"""Decode one default-DRC and one DRC-none short salient encoding control."""
import argparse,json,subprocess
from pathlib import Path
from validate import require,write_json
from validate_drc import workspace
from validate_portable import source_digest
from validate_replay import command,sha256_file


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True);p.add_argument('--default-control',type=Path,required=True);p.add_argument('--none-control',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    require(a.binary.is_file() and not a.report.exists(),'missing binary or existing report')
    r=dict(passed=False,profile='apac-hoa-salient-controls-v1',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=source_digest(),binary_sha256=sha256_file(a.binary),controls=[],errors=[],failure_directory=str(a.report.with_suffix('.failures')))
    try:
        for name,root in [('default',a.default_control),('none',a.none_control)]:
            manifest=root/'channel-solo/manifest.json';m=json.loads(manifest.read_text());source=root/'channel-solo/encoded.caf'
            require(m['complete'] and m['signal']=='channel-solo' and m['requested']['layout']=='hoa3' and m['requested']['sample_rate']==48000,'wrong control')
            require(m['requested']['frames']==96000,'requires the fixed short control')
            if name=='none':require(m['requested']['drc_configuration']=='none' and m['drc_configuration_verified'] is True and m['actual_encoder_settings']['cdrc']['value']==0,'DRC-none encoder request not verified')
            else:require(m['requested']['drc_configuration'] is None,'default control has an explicit DRC request')
            fingerprint=sha256_file(source)
            with workspace(r,name) as temporary:
                result=command(a.binary,'decode-sq',source,'--out',temporary/'pcm');pcm=temporary/'pcm/pcm.f32le';impl=result['pcm']['decoder_settings']['implementation']['value']
                require(result['saved_frames']==96000 and result['input']['consistency_verified'] and result['drc_payloads_complete'],'incomplete control decode')
                require(result['hoa_numeric_profile']=='apac-hoa-salient-math-v1' and result['drc_processing']==result['loudness_normalization']=='off','wrong decoder policy')
                require((result['drc_payload_frames']>0)==(name=='default'),'control DRC class differs')
                require(pcm.stat().st_size==96000*16*4,'coefficient PCM length differs')
                require(sha256_file(source)==fingerprint,'control changed')
                r['controls'].append(dict(kind=name,source_sha256=fingerprint,manifest_sha256=sha256_file(manifest),encoder_settings=m['actual_encoder_settings'],frames=result['saved_frames'],packets=result['packets'],embedded_frames=result['embedded_preroll_frames'],drc_payload_frames=result['drc_payload_frames'],pcm_sha256=sha256_file(pcm),implementation=impl,input=result['input']))
        require(source_digest()==r['source_sha256'] and sha256_file(a.binary)==r['binary_sha256'],'source/binary changed');r['passed']=True
    except Exception as e:r['errors'].append(str(e))
    write_json(a.report,r);print(json.dumps(dict(passed=r['passed'],controls=len(r['controls']),errors=r['errors'])));return 0 if r['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
