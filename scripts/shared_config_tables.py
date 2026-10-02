"""Immutable shared rate tables for independent format writers and oracles."""
import json
from functools import lru_cache
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RATES=[96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350]
@lru_cache(None)
def format():return json.loads((ROOT/'data/hoa-shared-config-format-v1.json').read_text())
def rate_info(rate):return next(r for r in format()['rates'] if r['sample_rate']==rate)
def offsets(rate,short,legacy):
    kind='short' if short else 'long'
    if rate in (44100,48000):return legacy[kind+'_offsets']
    key=rate_info(rate)[kind]
    return legacy[key.removeprefix('legacy-')+'_offsets'] if key.startswith('legacy-') else format()['offset_arrays'][key]
