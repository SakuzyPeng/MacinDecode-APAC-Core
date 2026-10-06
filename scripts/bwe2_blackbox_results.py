"""Freeze PCM-derived gain candidates and report LSF identifiability limits."""
import math
from pathlib import Path
import struct
import numpy as np

from bwe2_blackbox_wire import canonical,digest


def f32(value):
    return struct.unpack('<f',struct.pack('<f',value))[0]


def word(value):
    return struct.unpack('<I',struct.pack('<f',value))[0]


def grid_candidates(center,half):
    # Distinct binary32 values, not distinct decimal literals: coarse large
    # values can have multiple 1e-5 grid points rounding to the same word.
    ulp=float(np.spacing(np.float32(center)))
    lo,hi=center-half,center+half
    start=math.floor((lo-ulp)/1e-5)
    stop=math.ceil((hi+ulp)/1e-5)
    if stop-start>100000:
        raise ArithmeticError('unbounded grid interval')
    return sorted({word(f32(k/100000)) for k in range(start,stop+1) if lo<=f32(k/100000)<=hi})


def float_candidates(low,high):
    return [w for w in range(word(low)-1,word(high)+2)
            if low<=struct.unpack('<f',struct.pack('<I',w))[0]<=high]


def freeze_gains(gains,anchors,refined,dependencies):
    training=gains['estimates']
    if [r['seed'] for r in training]!=list(range(1,9)):
        raise ArithmeticError('gain freezing requires the eight predefined discovery seeds')
    absolute=np.array([r['absolute_reference']['gain'] for r in training])
    ratios=np.array([[v['relative_gain'] for v in r['rows']] for r in training])
    initial=ratios*absolute[:,None]
    initial_mean=initial.mean(0)
    calibration=max(r['relative_max_error'] for r in gains['calibration']['checks'])
    grid_anchors={}
    for i in (16,24,32,40,48,56):
        half=max(8*calibration*abs(initial_mean[i]),float(np.max(abs(initial[:,i]-initial_mean[i]))))
        words=grid_candidates(float(initial_mean[i]),half)
        if len(words)!=1:
            raise ArithmeticError(f'coarse anchor {i} is not unique on the hypothesis grid')
        grid_anchors[i]=dict(word=words[0],estimate=float(initial_mean[i]),half_width=half)
    intervals=[]
    for pair in ((0,0),(256,128)):
        rows=[r for r in anchors['estimates'] if tuple(r['pair'])==pair]
        if [r['seed'] for r in rows]!=list(range(1,65)):
            raise ArithmeticError('missing anchor ensemble measurements')
        values=np.array([[e['relative_gain'] for e in r['entries']] for r in rows])
        means=values.reshape(8,8,6).mean(1)
        mean=means.mean(0)
        half=8*np.max(abs(means-mean),axis=0)+1e-13
        for j,i in enumerate((16,24,32,40,48,56)):
            g=struct.unpack('<f',struct.pack('<I',grid_anchors[i]['word']))[0]
            intervals.append(dict(pair=pair,index=i,low=float(g/(mean[j]+half[j])),high=float(g/(mean[j]-half[j])),
                group_means=means[:,j].tolist(),raw_max_deviation=float(np.max(abs(values[:,j]-mean[j]))),
                ratio_mean=float(mean[j]),ratio_half_width=float(half[j])))
    low=max(r['low'] for r in intervals)
    high=min(r['high'] for r in intervals)
    ref_words=float_candidates(low,high) if low<=high else []
    if len(ref_words)!=1:
        raise ArithmeticError('absolute reference has ambiguous Float32 candidates')
    reference=struct.unpack('<f',struct.pack('<I',ref_words[0]))[0]
    rows=[]
    for i in range(64):
        if 57<=i<=62:
            samples=np.array([r['entries'][i-57]['relative_gain'] for r in refined['estimates']])
            if len(samples)!=64:raise ArithmeticError('missing high-gain repetitions')
            groups=samples.reshape(8,8).mean(1)
            ratio=float(groups.mean())
            half=(8*float(np.max(abs(groups-ratio)))+1e-13)*reference
        else:
            samples=ratios[:,i]
            ratio=float(samples.mean())
            half=(8*float(np.max(abs(samples-ratio)))+1e-13)*reference
        estimate=ratio*reference
        candidates=ref_words if i==63 else grid_candidates(estimate,half)
        rows.append(dict(index=i,estimate=estimate,half_width=0. if i==63 else half,
                         candidates_f32=candidates,determined=len(candidates)==1,
                         word=candidates[0] if len(candidates)==1 else None,
                         raw_max_deviation=float(np.max(abs(samples-ratio)))*reference))
    return dict(schema_version=1,kind='frozen-bwe2-gain-candidate',
        method='public AudioConverter PCM; monic spectral factorization and calibrated gain ratios',
        hypothesis=dict(decimal_grid='0.00001',qualification='conditional on independently inferred grid; pending held-out validation'),
        reference_index=63,reference_interval=[low,high],reference_candidates_f32=ref_words,
        coarse_anchors=grid_anchors,anchor_intervals=intervals,rows=rows,
        all_determined=all(r['determined'] for r in rows),dependencies=dependencies)


def lsf_identifiability(probe,dependency):
    values={tuple(r['pair']):np.array(r['lsf']) for r in probe['estimates'] if 'lsf' in r}
    indices=[0,17,73,127,256,401,511]
    if len(values)!=49:
        raise ArithmeticError('LSF pilot did not yield every fitted composite')
    # This is a witness for the decomposition ambiguity of the observed-sum
    # model, not a claim to have recovered either original stage dictionary.
    first=np.array([values[(i,0)] for i in indices],np.float32)
    second=np.array([values[(0,j)]-values[(0,0)] for j in indices],np.float32)
    original=(first[:,None,:]+second[None,:,:]).astype(np.float32)
    shifted_first=(first+np.float32(1)).astype(np.float32)
    shifted_second=(second-np.float32(1)).astype(np.float32)
    shifted=(shifted_first[:,None,:]+shifted_second[None,:,:]).astype(np.float32)
    equal=bool(np.array_equal(original.view(np.uint32),shifted.view(np.uint32)))
    if not equal:raise ArithmeticError('finite-precision ambiguity witness failed')
    errors=[]
    for a,i in enumerate(indices):
        for b,j in enumerate(indices):
            pred=original[a,b].astype(float)
            for k in range(16):pred[k]=max(pred[k],(pred[k-1] if k else 0)+50)
            if pred[-1]>11950:
                for k in range(15,-1,-1):pred[k]=min(pred[k],(pred[k+1] if k<15 else 12000)-50)
            errors.append(dict(pair=[i,j],max_frequency_error=float(np.max(abs(pred-values[(i,j)])))))
    return dict(schema_version=1,kind='lsf-observability-result',status='incomplete',
        coverage=dict(pairs=49,composite_frequency_estimates=49*16,full_stage_entries=0),
        original_dictionaries_identified=False,eligible_lsf=False,
        blockers=['stagewise additive-offset ambiguity is not identified by the current PCM experiment',
                  'conditioning loses information; axis sums do not reliably reconstruct unconditioned cross-pairs',
                  'fitted composite frequencies are continuous estimates, not uniquely determined raw Float32 words'],
        dependency=dependency,indices=indices,axis_cross_checks=errors,
        witness=dict(shift=1.,exact_float32_sum_words=original.size,
            first_f32=first.view(np.uint32).tolist(),second_f32=second.view(np.uint32).tolist(),
            alternative_first_f32=shifted_first.view(np.uint32).tolist(),alternative_second_f32=shifted_second.view(np.uint32).tolist(),
            sum_sha256=digest(original.astype('<f4').tobytes()),
            qualification='two decompositions of the pilot axis-sum model; neither is certified as an original codebook; this does not prove impossibility for every possible public experiment'),
        decision='do not sweep all 1024 raw vectors until an independent stagewise anchor and conditioning inversion have been identified')
