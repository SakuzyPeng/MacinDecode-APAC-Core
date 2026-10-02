#!/usr/bin/env python3
"""Normalize bounded shared DRC wire codebooks; no audio gain processing."""
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];PATH=ROOT/'data/hoa-shared-drc-format-v1.json'
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def generate(facts):
 result=dict(format_profile='apac-hoa-shared-drc-syntax-v1',normal=[dict(width=r['width'],code=r['code'],value=r['value']) for r in facts['normal']],clipping=[dict(width=r['width'],code=r['code'],value=r['value']) for r in facts['clipping']],slopes=[dict(width=r['width'],code=r['code'],value=r['index']) for r in facts['slopes']],source=dict(component='AudioCodecs 7.0',component_sha256='826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd',method='bounded UniDRC wire constants; independently generated gains; no playback processing'))
 result['format_sha256']=hashlib.sha256(canonical(result)).hexdigest();return result
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--facts',type=Path);p.add_argument('--check',action='store_true');a=p.parse_args()
 if a.facts:
  result=generate(json.loads(a.facts.read_text()))
  if a.check:assert json.loads(PATH.read_text())==result
  else:PATH.write_text(json.dumps(result,indent=2)+'\n')
 else:
  assert a.check;v=json.loads(PATH.read_text());sha=v.pop('format_sha256');assert hashlib.sha256(canonical(v)).hexdigest()==sha
