"""Portable validation utilities migrated from the research suite."""
from pathlib import Path
import hashlib
import itertools
import json
import subprocess
from validate import require
from spectrum_vectors import frame, matrix_cases

def sequences():
    for case in matrix_cases():
        yield case['kind'], [{}, case, {}, {}]
    for rate_block in [1, 2, 3]:
        for band in range(14 if rate_block == 2 else 49):
            for side in ['left', 'right']:
                cb = 1 + band % 11
                values = [1,-1,0,1] if cb <= 4 else [16,-17] if cb == 11 else [1,-1]
                active = dict(block=rate_block, grouping=[0,0x55,0x7f][band%3], **{side:{band:(cb,values,160)}})
                sequence = [{},dict(block=1),dict(block=2),dict(block=3),{},{}]
                sequence[rate_block] = active
                yield 'window_transition', sequence
    yield 'overlap', [{},dict(left={0:(1,[1,0,0,0],160)}),dict(left={48:(11,[16,-17],160)},right={1:(5,[1,-1],160)}),{},{}]
    high=dict(gain=255,left={0:(1,[-1,1,-1,1],255)},right={1:(2,[1,-1,1,-1],255)})
    yield 'overlap_pressure',[{},high,high,{},{}]
    yield 'short_pressure',[{},dict(block=1),dict(high,block=2),dict(block=3),{},{}]
    yield 'mixed_windows',[{},dict(high,block=1,right_block=0),dict(block=2,right_block=0),dict(block=3,right_block=0),{},{}]
    for values in itertools.product([-1,1],repeat=4):
        signal=dict(gain=255,left={0:(1,list(values),255)})
        yield 'heldout_sign_overlap',[{},signal,signal,{},{}]

def batch_cases(items, count=48):
    batch=[]
    for item in items:
        batch.append(item)
        if len(batch)==count:
            yield batch
            batch=[]
    if batch:
        yield batch
