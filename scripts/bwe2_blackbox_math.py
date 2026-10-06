"""PCM-only spectral and monic-filter recovery, with no target dictionaries."""
from functools import lru_cache
import numpy as np


def pcm(raw):
    return np.frombuffer(raw,dtype='<f4').reshape(-1,2).astype(float)


@lru_cache(maxsize=1)
def basis():
    t=np.arange(2048)+.5
    return np.sin(np.pi*t/2048)[:,None]*np.cos(np.pi/1024*(t[:,None]+512)*(np.arange(1024)+.5)[None,:])


def spectrum(raw, scale):
    return basis().T@pcm(raw)[:,0]/(512*scale)


def lsf_to_lpc(frequencies):
    products=[]
    for parity in (0,1):
        p=np.array([1.])
        for f in frequencies[parity::2]:
            p=np.convolve(p,[1.,-2*np.cos(np.pi*f/12000),1.])
        products.append(p)
    p,q=products
    return ((np.r_[p,0]+np.r_[0,p]+np.r_[q,0]-np.r_[0,q])/2)[:17]


def lpc_to_lsf(a):
    padded=np.r_[a,0.]
    pairs=[]
    for sign in (1,-1):
        roots=np.roots(padded+sign*padded[::-1])
        upper=roots[roots.imag>1e-7]
        if len(upper)!=8 or np.max(abs(abs(upper)-1))>1e-5:
            raise ArithmeticError('filter has no unique stable interlaced LSF representation')
        pairs.append(np.sort(np.angle(upper)*12000/np.pi))
    values=np.array([pairs[i%2][i//2] for i in range(16)])
    if np.any(np.diff(values)<=0):
        raise ArithmeticError('LSF roots do not interlace')
    return values


def fit_envelope(values, carrier, cutoff=256):
    """Recover a monic stable filter and absolute gain for a flat-power comb.

    The carrier's first 16 autocorrelations are zero. Thus its LPC is one,
    and the observed squared reciprocal transfer is a cosine polynomial
    proportional to |A|^2. Stable spectral factorization fixes the scale.
    """
    ids=np.arange(1,512,2)
    source=carrier[128+ids%(cutoff-128)]
    transfer=values[cutoff+ids]/source
    if np.any(~np.isfinite(transfer)) or np.any(transfer<=0):
        raise ArithmeticError('nonpositive extension transfer')
    return fit_transfer(ids,transfer)


def fit_transfer(ids,transfer):
    """Stable spectral factor from positive response at at least 17 bins."""
    ids=np.asarray(ids);transfer=np.asarray(transfer,float)
    if ids.ndim!=1 or transfer.shape!=ids.shape or len(ids)<17 or len(set(ids))!=len(ids):
        raise ValueError('at least 17 distinct transfer bins required')
    if np.any(ids<0) or np.any(ids>=512) or np.any(~np.isfinite(transfer)) or np.any(transfer<=0):
        raise ArithmeticError('invalid transfer observation')
    power=transfer**-2
    design=np.cos(np.pi/512*ids[:,None]*np.arange(17)[None,:])
    design[:,1:]*=2
    # Weighting by observed power avoids giving the least audible bins the
    # largest influence. This choice is independent of any target table.
    weight=1/np.maximum(power,np.median(power)*1e-3)
    coeff=np.linalg.lstsq(design*weight[:,None],power*weight,rcond=None)[0]
    roots=np.roots(np.r_[coeff[16:0:-1],coeff])
    ordered=roots[np.argsort(abs(roots))]
    inside=ordered[:16]
    if not (np.max(abs(inside))<1 and np.min(abs(ordered[16:]))>1):
        raise ArithmeticError('ambiguous stable spectral factor')
    complex_a=np.poly(inside)
    if np.max(abs(complex_a.imag))>1e-7:
        raise ArithmeticError('non-real spectral factor')
    a=complex_a.real
    factor=coeff[0]/float(a@a)
    if factor<=0:
        raise ArithmeticError('nonpositive gain estimate')
    gain=float(1/np.sqrt(factor))
    lsf=lpc_to_lsf(a)
    fit=design@coeff
    return dict(gain=gain,lsf=lsf.tolist(),lpc=a.tolist(),
                relative_power_residual=float(np.max(abs(fit-power))/np.max(power)),
                relative_transfer_rms=float(np.sqrt(np.mean((np.sqrt(np.maximum(fit,0))*transfer-1)**2))),
                max_root_radius=float(np.max(abs(inside))))


def envelope(lsf, bins=512):
    a=lsf_to_lpc(lsf)
    w=np.pi*np.arange(bins)/bins
    return abs(np.exp(-1j*w[:,None]*np.arange(17)[None,:])@a)


def affine_scale(value, baseline, reference):
    direction=reference-baseline
    coefficient=float(direction@(value-baseline)/(direction@direction))
    residual=value-baseline-coefficient*direction
    return coefficient,float(np.linalg.norm(residual)/max(np.linalg.norm(value-baseline),1e-300))
