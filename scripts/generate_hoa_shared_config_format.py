#!/usr/bin/env python3
"""Package shared rate/SFB/TNS facts without duplicating the legacy band arrays.

The band offsets and TNS limits are the AAC tables; `--vo-aacenc DIR` rebuilds them from a
checkout of vo-aacenc (Apache-2.0) at the commit pinned in generate_sq_codebooks.py and
requires the result to equal the committed file. The profile/level table is not an AAC table:
it is carried over from the committed file. The `source` record is part of the published
`format_sha256` identity and stays as first recorded.
"""
import argparse,hashlib,json
from pathlib import Path
from generate_sq_codebooks import COMMIT, array
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'data/hoa-shared-config-format-v1.json'
RATES=[96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350]
# vo-aacenc sampling-index order (sampRateTab); 7350 Hz uses the 8000 Hz bucket.
VO_RATES=[96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000]
VO_SHA256={'aacenc/src/aac_rom.c':'bebae3b53f4de70055c6328a24e3fc3172f3b15d40eb9e4556d6d8c2d91cb8b6',
           'aacenc/src/tns.c':'efb48e5a89a2e4a886d838630c2c95a865cda422d9e29e02079e9014e46c566b'}
SOURCE=dict(component='AudioCodecs 7.0',component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',method='bounded decoder SFB/TNS tables and shared configuration pseudocode; native rate controls')

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
    result=dict(format_profile='apac-hoa-shared-configuration-v1',rates=rows,offset_arrays=arrays,profiles=profiles,frame_samples=1024,spatial_method_1_required_long_bands=49,source=SOURCE)
    result['format_sha256']=hashlib.sha256(canonical(result)).hexdigest();return result

def vo_aacenc(directory):
    """SFB offsets and Main/LC TNS band limits per rate from the pinned vo-aacenc sources."""
    text={}
    for name,digest in VO_SHA256.items():
        raw=(directory/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise AssertionError(f'{name} is not the file at vo-aacenc {COMMIT}')
        text[name]=raw.decode()
    rom,tns=text['aacenc/src/aac_rom.c'],text['aacenc/src/tns.c']
    def bands(kind):
        table,offsets,totals=array(rom,f'sfBandTab{kind}'),array(rom,f'sfBandTab{kind}Offset'),array(rom,f'sfBandTotal{kind}')
        return [table[o:o+n+1] for o,n in zip(offsets,totals)]
    sfb=[dict(sample_rate=r,long_offsets=l,short_offsets=s) for r,l,s in zip(VO_RATES,bands('Long'),bands('Short'))]
    limits=[dict(rate=r,long_limit=l,short_limit=s) for r,l,s in zip(VO_RATES,array(tns,'tnsMaxBandsLongMainLow'),array(tns,'tnsMaxBandsShortMainLow'))]
    return sfb,limits

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--vo-aacenc',type=Path,help=f'vo-aacenc checkout at {COMMIT}');p.add_argument('--output',type=Path);p.add_argument('--check',action='store_true');a=p.parse_args()
    committed=json.loads(PATH.read_text());sha=committed['format_sha256'];body={k:v for k,v in committed.items() if k!='format_sha256'}
    assert hashlib.sha256(canonical(body)).hexdigest()==sha,'embedded format_sha256 does not match the file'
    if a.vo_aacenc is not None:
        result=generate(*vo_aacenc(a.vo_aacenc),committed['profiles']);data=(json.dumps(result,indent=2)+'\n').encode()
        if a.check:assert data==PATH.read_bytes(),'SFB/TNS tables differ from the vo-aacenc regeneration'
        else:
            with (a.output or p.error('generation requires --output')).open('xb') as out:out.write(data)
    else:assert a.check,'--vo-aacenc is required to regenerate'
    print(json.dumps(dict(format_sha256=sha,regenerated=a.vo_aacenc is not None)))
