#!/usr/bin/env python3
"""Package shared rate/SFB/TNS facts without duplicating the legacy band arrays."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'data/hoa-shared-config-format-v1.json'
RATES=[96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350]

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def generate(sfb,tns,profiles):
    old=json.loads((ROOT/'data/sq-codebooks.json').read_text());arrays={};rows=[];limits={r['rate']:r for r in tns}
    def identity(values,kind):
        if values==old[kind+'_offsets']:return 'legacy-'+kind
        key=hashlib.sha256(canonical(values)).hexdigest();arrays[key]=values;return key
    lookup={r['sample_rate']:r for r in sfb}
    for rate in RATES:
        bucket=8000 if rate==7350 else rate;entry=lookup[bucket];limit=limits[bucket]
        rows.append(dict(sample_rate=rate,sfb_rate=bucket,long=identity(entry['long_offsets'],'long'),short=identity(entry['short_offsets'],'short'),tns_long_limit=limit['long_limit'],tns_short_limit=limit['short_limit']))
    result=dict(format_profile='apac-hoa-shared-configuration-v1',rates=rows,offset_arrays=arrays,profiles=profiles,frame_samples=1024,spatial_method_1_required_long_bands=49,source=dict(component='AudioCodecs 7.0',component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',method='bounded decoder SFB/TNS tables and shared configuration pseudocode; native rate controls'))
    result['format_sha256']=hashlib.sha256(canonical(result)).hexdigest();return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sfb',type=Path);p.add_argument('--tns',type=Path);p.add_argument('--profiles',type=Path);p.add_argument('--check',action='store_true');a=p.parse_args()
    if all(v is not None for v in (a.sfb,a.tns,a.profiles)):
        result=generate(*[json.loads(x.read_text()) for x in (a.sfb,a.tns,a.profiles)])
        if a.check:assert json.loads(PATH.read_text())==result
        else:PATH.write_text(json.dumps(result,indent=2)+'\n')
    else:
        assert a.check and all(v is None for v in (a.sfb,a.tns,a.profiles));result=json.loads(PATH.read_text());sha=result.pop('format_sha256');assert hashlib.sha256(canonical(result)).hexdigest()==sha
