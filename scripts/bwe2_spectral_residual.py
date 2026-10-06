"""Conditional Float32-lattice inference from small, calibrated PCM residuals.

This is an empirical synthesis-error model, not a proof about arbitrary native
rounding. Exact zeros are retained separately and must agree with the inference.
"""
import numpy as np
from bwe2_blackbox_math import basis


def infer(samples,candidates,targets,scale,relative_bound=1e-5):
    samples=np.asarray(samples,float);candidates=np.asarray(candidates,np.uint32)
    if samples.ndim!=2 or samples.shape[0]!=2048 or candidates.shape!=(samples.shape[1],len(targets)):
        raise ValueError('residual and candidate dimensions differ')
    if not np.all(np.isfinite(samples)) or not scale>0 or not 0<relative_bound<=1e-3:
        raise ValueError('invalid residual calibration')
    matrix=basis()[:,targets]*scale
    if np.linalg.cond(matrix)>2:raise ArithmeticError('residual basis is ill conditioned')
    delta=np.linalg.lstsq(matrix,samples,rcond=None)[0].T
    estimated=candidates.view(np.float32).astype(float)-delta
    if np.any(estimated<=0) or np.any(~np.isfinite(estimated)):raise ArithmeticError('nonpositive inferred response')
    rounded=estimated.astype(np.float32);words=rounded.view(np.uint32)
    ulps=np.spacing(rounded).astype(float)
    residual=samples-matrix@delta.T
    shape=np.linalg.norm(residual,axis=0)/np.maximum(np.linalg.norm(samples,axis=0),1e-300)
    width=relative_bound*np.sum(abs(delta),axis=1)[:,None]+np.finfo(float).eps*np.max(estimated)*16
    distance=abs(estimated-rounded.astype(float))
    zero=np.where(np.all(samples==0,axis=0))[0]
    consistent=bool(np.all(words==words[:1]))
    qualified=bool(consistent and np.all(shape<relative_bound) and np.all(width/ulps<.125)
                   and np.all((distance+width)/ulps<.125))
    if len(zero) and (not consistent or np.any(candidates[zero]!=words[:1])):
        raise ArithmeticError('exact zero conflicts with the residual lattice')
    return dict(words=words[0].tolist() if consistent else None,consistent_channels=consistent,
                qualified=qualified,exact_zero_channels=zero.tolist(),
                exact_zero_words=candidates[zero[0]].tolist() if len(zero) else None,
                max_bound_in_ulp=np.max(width/ulps,axis=0).tolist(),
                max_distance_in_ulp=np.max(distance/ulps,axis=0).tolist(),
                max_shape_error=float(max(shape)),relative_error_hypothesis=relative_bound,
                qualification='conditional on the empirical residual-synthesis error bound; not original LSF words')


def calibrate(samples,targets):
    """One two-packet unit tone for each target, with repeated output channels."""
    samples=np.asarray(samples,float)
    if samples.ndim!=3 or samples.shape[:2]!=(len(targets),2048):raise ValueError('unit tone shape differs')
    scales=[];shapes=[]
    for raw,line in zip(samples,targets):
        if not np.array_equal(raw,raw[:,:1]+np.zeros_like(raw)):raise ArithmeticError('unit output channels differ')
        expected=basis()[:,line];scale=float(expected@raw[:,0]/(expected@expected))
        if not scale>0:raise ArithmeticError('nonpositive synthesis calibration')
        scales.append(scale);shapes.append(float(np.linalg.norm(raw[:,0]-scale*expected)/np.linalg.norm(raw[:,0])))
    scale=float(np.mean(scales));maximum=max(max(shapes),max(abs(np.array(scales)/scale-1)))
    if maximum>=1e-5/8:raise ArithmeticError('unit calibration exceeds the eightfold error margin')
    return dict(scale=scale,per_line_scales=scales,max_relative_error=maximum,
                relative_residual_bound=1e-5,empirical_safety_factor=8,targets=list(targets))
