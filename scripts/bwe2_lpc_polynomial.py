#!/usr/bin/env python3
"""Independent rounded-polynomial hypotheses for PCM-inferred LPC vectors."""
import argparse
import itertools
import json
from pathlib import Path
import time
import ctypes
import bwe2_blackbox
import numpy as np
from bwe2_blackbox_capture import atomic
from bwe2_blackbox_math import lpc_to_lsf
from bwe2_blackbox_wire import canonical,digest


def cosine_values(frequencies,kind):
    frequencies=np.asarray(frequencies,np.float32)
    if kind=='double':return np.float32(np.cos(frequencies.astype(float)*np.pi/12000))
    if kind=='float-angle':return np.cos(np.float32(frequencies*np.float32(np.pi/12000)))
    if kind.endswith('double-angle'):angle=np.float32(frequencies.astype(float)*(np.pi/12000))
    elif kind.endswith('divide-first'):angle=np.float32(np.float32(frequencies/np.float32(12000))*np.float32(np.pi))
    elif kind.endswith('multiply-first'):angle=np.float32(np.float32(frequencies*np.float32(np.pi))/np.float32(12000))
    else:angle=np.float32(frequencies*np.float32(np.pi/12000))
    if kind.startswith('vforce'):
        from bwe2_public_math import PublicMath
        return PublicMath().unary('vvcosf',angle)
    lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib');lib.cosf.argtypes=[ctypes.c_float];lib.cosf.restype=ctypes.c_float
    return np.array([lib.cosf(float(x)) for x in angle],np.float32)


COSINE_HYPOTHESES=('double','float-angle','vforce-float-angle','vforce-double-angle',
                   'vforce-divide-first','vforce-multiply-first','cosf-float-angle','cosf-double-angle')


def full_polynomials(cosines,order,arithmetic):
    """A generic quadratic product may lose exact symmetry when rounded."""
    x=np.asarray(cosines,np.float32)[:,order];p=np.ones((len(x),1),np.float32)
    fused=arithmetic.startswith('fused')
    for k in range(8):
        old=np.pad(p,((0,0),(0,2)));prev=np.pad(p,((0,0),(1,1)));older=np.pad(p,((0,0),(2,0)))
        coefficient=np.float32(-2*x[:,k,None]);product=np.float32(coefficient*prev)
        if fused:
            fma=lambda a,b,c:np.float32(a.astype(float)*b.astype(float)+c.astype(float))
            if arithmetic.endswith('sum'):p=fma(coefficient,prev,np.float32(old+older))
            elif arithmetic.endswith('older'):p=np.float32(old+fma(coefficient,prev,older))
            else:p=np.float32(fma(coefficient,prev,old)+older)
        elif arithmetic.endswith('sum'):p=np.float32(np.float32(old+older)+product)
        elif arithmetic.endswith('older'):p=np.float32(old+np.float32(product+older))
        else:p=np.float32(np.float32(old+product)+older)
    return p


def half_polynomials(cosines,order,arithmetic):
    if arithmetic.startswith('full-'):return full_polynomials(cosines,order,arithmetic[5:])
    if arithmetic=='double-product':
        x=np.asarray(cosines,float)[:,order];p=np.ones((len(x),1))
        for k in range(8):
            p=np.pad(p,((0,0),(0,2)))+np.pad(-2*x[:,k,None]*p,((0,0),(1,1)))+np.pad(p,((0,0),(2,0)))
        return np.float32(p[:,:9])
    x=np.asarray(cosines,dtype=np.float32)[:,order]
    if arithmetic.startswith('tree-'):
        blocks=[np.stack([np.ones(len(x),np.float32),np.float32(-2*x[:,i]),np.ones(len(x),np.float32)],axis=1) for i in range(8)]
        fused='fused' in arithmetic;reverse=arithmetic.endswith('reverse')
        while len(blocks)>1:
            combined=[]
            for left,right in zip(blocks[::2],blocks[1::2]):
                degree=left.shape[1]+right.shape[1]-2;result=np.zeros((len(x),degree+1),np.float32)
                for k in range(degree//2+1):
                    terms=list(range(max(0,k-right.shape[1]+1),min(k+1,left.shape[1])))
                    if reverse:terms.reverse()
                    acc=np.zeros(len(x),np.float32)
                    for i in terms:
                        acc=np.float32(left[:,i].astype(float)*right[:,k-i].astype(float)+acc.astype(float)) if fused else np.float32(acc+np.float32(left[:,i]*right[:,k-i]))
                    result[:,k]=acc;result[:,degree-k]=acc
                combined.append(result)
            blocks=combined
        return blocks[0][:,:9]
    p=np.zeros((len(x),9),np.float32);p[:,0]=1
    fused=arithmetic.startswith('fused');sum_first=arithmetic.endswith('sum')
    fma=lambda a,b,c:np.float32(a.astype(float)*b.astype(float)+c.astype(float))
    for stage in range(1,9):
        coefficient=np.float32(-2*x[:,stage-1])
        if stage==1:p[:,1]=coefficient;continue
        p[:,stage]=fma(coefficient,p[:,stage-1],np.float32(2*p[:,stage-2])) if fused else np.float32(np.float32(coefficient*p[:,stage-1])+np.float32(2*p[:,stage-2]))
        for j in range(stage-1,1,-1):
            if fused:
                if arithmetic.endswith('older'):p[:,j]=np.float32(p[:,j]+fma(coefficient,p[:,j-1],p[:,j-2]))
                else:p[:,j]=fma(coefficient,p[:,j-1],np.float32(p[:,j]+p[:,j-2])) if sum_first else np.float32(fma(coefficient,p[:,j-1],p[:,j])+p[:,j-2])
            else:
                product=np.float32(coefficient*p[:,j-1])
                if arithmetic.endswith('older'):p[:,j]=np.float32(p[:,j]+np.float32(product+p[:,j-2]))
                else:p[:,j]=np.float32(np.float32(p[:,j]+p[:,j-2])+product) if sum_first else np.float32(np.float32(p[:,j]+product)+p[:,j-2])
        p[:,1]=np.float32(p[:,1]+coefficient)
    return p


def analyze(a):
    frequency=lpc_to_lsf(a.astype(float));grid=np.round(frequency,2)
    targets=[]
    for parity in (0,1):
        values=np.zeros(9,np.float32);values[0]=1
        for i in range(1,9):
            values[i]=np.float32(np.float32(a[i]+a[17-i])-values[i-1]) if parity==0 else np.float32(np.float32(a[i]-a[17-i])+values[i-1])
        targets.append(values)
    orders=dict(ascending=list(range(8)),descending=list(range(7,-1,-1)),outside_in=[0,7,1,6,2,5,3,4],
                inside_out=[3,4,2,5,1,6,0,7],bit_reversed=[0,4,2,6,1,5,3,7])
    results=[]
    for cosine in ('double','float-angle'):
        for parity in (0,1):
            choices=[]
            for f in grid[parity::2]:
                center=np.float32(f).view(np.uint32).item()
                variants=np.array([center-1,center,center+1],dtype=np.uint32).view(np.float32)
                values=np.float32(np.cos(variants.astype(float)*np.pi/12000)) if cosine=='double' else np.cos(np.float32(variants*np.float32(np.pi/12000)))
                choices.append(sorted(set(map(float,values))))
            candidates=np.array(list(itertools.product(*choices)),np.float32)
            for name,order in orders.items():
                for arithmetic in ('separate-sum','separate-product','fused-sum','fused-product'):
                    polynomials=half_polynomials(candidates,order,arithmetic)
                    difference=polynomials.astype(float)-targets[parity].astype(float)
                    score=np.sum(difference**2,axis=1);best=int(np.argmin(score))
                    results.append(dict(parity=parity,cosine=cosine,order=name,arithmetic=arithmetic,
                        configurations=len(candidates),maximum_coefficient_error=float(np.max(abs(difference[best]))),
                        squared_error=float(score[best]),cosines=candidates[best].tolist(),polynomial=polynomials[best].tolist(),
                        exact_polynomial_matches=int(np.count_nonzero(np.all(polynomials.view(np.uint32)==targets[parity].view(np.uint32),axis=1)))))
    return dict(continuous_frequency=frequency.tolist(),grid_hypothesis='0.01',grid_centers=grid.tolist(),
        uncertainty='grid and pre-cosine rounding assumptions are unqualified; no old LSF values used',
        p_q_targets=[p.tolist() for p in targets],hypotheses=results,eligible_lsf=False)


def assemble(p,q,kind):
    # p/q contain the lower half of symmetric degree-16 polynomials.
    if p.shape[1]==9:p=np.concatenate([p,p[:,7::-1]],axis=1)
    if q.shape[1]==9:q=np.concatenate([q,q[:,7::-1]],axis=1)
    if p.shape[1]!=17 or q.shape[1]!=17:raise ValueError('degree-16 polynomial required')
    a=np.empty_like(p);a[:,0]=1
    if kind=='grouped':a[:,1:]=np.float32(np.float32(np.float32(p[:,1:]+q[:,1:])+np.float32(p[:,:-1]-q[:,:-1]))*np.float32(.5))
    elif kind=='left':a[:,1:]=np.float32(np.float32(np.float32(np.float32(p[:,1:]+q[:,1:])+p[:,:-1])-q[:,:-1])*np.float32(.5))
    else:a[:,1:]=np.float32(np.float32(np.float32(p[:,1:]+p[:,:-1])+np.float32(q[:,1:]-q[:,:-1]))*np.float32(.5))
    return a


def coupled_search(a,cent_radius=0,tree=False,public_cosines=False,full=False,double_polys=False):
    frequencies=lpc_to_lsf(a.astype(float));grid=np.round(frequencies,2);results=[]
    orders=dict(ascending=list(range(8)),descending=list(range(7,-1,-1)),outside_in=[0,7,1,6,2,5,3,4],
                inside_out=[3,4,2,5,1,6,0,7],bit_reversed=[0,4,2,6,1,5,3,7])
    for cosine in (COSINE_HYPOTHESES if public_cosines else ('double','float-angle')):
        candidates=[]
        for parity in (0,1):
            choices=[]
            for position,f in enumerate(grid[parity::2]):
                centers=[f+.01*k for k in range(-cent_radius,cent_radius+1)] if position<2 else [f]
                variants=[]
                for center in centers:
                    w=np.float32(center).view(np.uint32).item();variants.extend(np.array([w-1,w,w+1],np.uint32).view(np.float32).tolist())
                variants=np.array(variants,np.float32)
                values=cosine_values(variants,cosine)
                choices.append(sorted(set(map(float,values))))
            candidates.append(np.array(list(itertools.product(*choices)),np.float32))
        for order_name,order in orders.items():
            methods=('double-product',) if double_polys else ('tree-separate-forward','tree-separate-reverse','tree-fused-forward','tree-fused-reverse') if tree else ('separate-sum','separate-product','separate-older','fused-sum','fused-product','fused-older')
            for arithmetic in methods:
                if full:arithmetic='full-'+arithmetic
                polys=[half_polynomials(c,order,arithmetic) for c in candidates]
                for final in ('grouped','left','symmetric'):
                    matches=[];maximum_prefix=0;best_error=float('inf')
                    for start in range(0,len(polys[0]),64):
                        # First coefficient is (p1+q1)/2 for each association.
                        lhs=polys[0][start:start+64,1,None];rhs=polys[1][None,:,1]
                        initial=np.float32(lhs+rhs)
                        if final=='left':initial=np.float32(np.float32(initial+np.float32(1))-np.float32(1))
                        elif final=='symmetric':initial=np.float32(np.float32(lhs+np.float32(1))+np.float32(rhs-np.float32(1)))
                        mask=np.float32(initial*np.float32(.5))==a[1]
                        i,j=np.where(mask);i+=start
                        if not len(i):continue
                        predicted=assemble(polys[0][i],polys[1][j],final)
                        equal=predicted.view(np.uint32)==a.view(np.uint32)
                        prefix=np.cumprod(equal[:,1:].astype(int),axis=1).sum(axis=1)
                        maximum_prefix=max(maximum_prefix,int(max(prefix)))
                        best_error=min(best_error,float(np.min(np.max(abs(predicted.astype(float)-a.astype(float)),axis=1))))
                        for ix in np.where(np.all(equal,axis=1))[0]:
                            matches.append(dict(cosines_even=candidates[0][i[ix]].tolist(),cosines_odd=candidates[1][j[ix]].tolist(),
                                                p=polys[0][i[ix]].tolist(),q=polys[1][j[ix]].tolist()))
                    results.append(dict(cosine=cosine,order=order_name,arithmetic=arithmetic,final=final,cent_radius=cent_radius,
                        configurations=[len(c) for c in candidates],max_matching_prefix=maximum_prefix,
                        best_max_lpc_error=best_error if np.isfinite(best_error) else None,matches=matches))
    return dict(grid_centers=grid.tolist(),cent_radius=cent_radius,hypotheses=results,eligible_lsf=False)


def broad_search(a):
    """Wider low-frequency centers, with a declared bounded shortlist.

    Polynomial targets rank candidates only. A match still requires every
    assembled Float32 LPC word to agree; absence is not an impossibility proof.
    """
    grid=np.round(lpc_to_lsf(a.astype(float)),2);targets=[]
    for parity in (0,1):
        p=np.zeros(9);p[0]=1
        for i in range(1,9):
            p[i]=float(a[i])+float(a[17-i])-p[i-1] if parity==0 else float(a[i])-float(a[17-i])+p[i-1]
        targets.append(p)
    results=[]
    for cosine in ('vforce-float-angle','vforce-double-angle','cosf-float-angle','double'):
        candidates=[]
        for parity in (0,1):
            choices=[]
            for position,f in enumerate(grid[parity::2]):
                variants=[]
                for cent in ((-1,0,1) if position<4 else (0,)):
                    word=np.float32(f+.01*cent).view(np.uint32).item()
                    variants.extend(np.array([word-1,word,word+1],np.uint32).view(np.float32).tolist())
                choices.append(sorted(set(map(float,cosine_values(variants,cosine)))))
            candidates.append(np.array(list(itertools.product(*choices)),np.float32))
        for order_name,order in (('ascending',list(range(8))),('descending',list(range(7,-1,-1)))):
            for arithmetic in ('separate-sum','separate-product','separate-older','fused-sum','fused-product','fused-older'):
                short=[];counts=[]
                for parity in (0,1):
                    polys=half_polynomials(candidates[parity],order,arithmetic)
                    error=np.sum((polys.astype(float)-targets[parity])**2,axis=1)
                    count=min(1024,len(error));selected=np.argpartition(error,count-1)[:count]
                    short.append((polys[selected],candidates[parity][selected]));counts.append(len(error))
                for final in ('grouped','left','symmetric'):
                    best=float('inf');matches=[];prefix_best=0
                    for start in range(0,len(short[0][0]),32):
                        left=short[0][0][start:start+32];right=short[1][0]
                        n,m=len(left),len(right)
                        prediction=assemble(np.repeat(left,m,axis=0),np.tile(right,(n,1)),final)
                        error=np.max(abs(prediction.astype(float)-a.astype(float)),axis=1)
                        best=min(best,float(np.min(error)))
                        equal=prediction.view(np.uint32)==a.view(np.uint32)
                        prefix_best=max(prefix_best,int(np.max(np.cumprod(equal[:,1:].astype(int),axis=1).sum(axis=1))))
                        for flat in np.where(np.all(equal,axis=1))[0]:
                            i,j=start+int(flat)//m,int(flat)%m
                            matches.append(dict(cosines_even=short[0][1][i].tolist(),cosines_odd=short[1][1][j].tolist()))
                    results.append(dict(cosine=cosine,order=order_name,arithmetic=arithmetic,final=final,
                        configurations=counts,shortlisted=[len(s[0]) for s in short],max_matching_prefix=prefix_best,best_max_lpc_error=best,matches=matches))
            print(json.dumps(dict(cosine=cosine,order=order_name,best=min(r['best_max_lpc_error'] for r in results),
                                  exact=sum(len(r['matches']) for r in results))),flush=True)
    return dict(grid_centers=grid.tolist(),hypotheses=results,eligible_lsf=False,
        qualification='unqualified .01-Hz grid; first four roots per parity allow +/- .01 Hz and +/- one Float32 input step; only 1024 ranked half-polynomials paired')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--profile',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--coupled',action='store_true');parser.add_argument('--cent-radius',type=int,default=0,choices=(0,1));parser.add_argument('--tree',action='store_true')
    parser.add_argument('--public-cosines',action='store_true')
    parser.add_argument('--broad',action='store_true')
    parser.add_argument('--full',action='store_true',help='allow generic quadratic products to accumulate asymmetric rounding')
    parser.add_argument('--double-polys',action='store_true',help='cast double-precision partial polynomials before Float32 assembly')
    args=parser.parse_args();raw=args.profile.read_bytes();profile=json.loads(raw)
    words=profile.get('lpc_f32')
    if words is None:words=min(profile['fitted'],key=lambda r:r['mse_ulp'])['lpc_f32']
    a=np.array(words,np.uint32).view(np.float32)
    if args.full and (args.tree or args.broad or not args.coupled):parser.error('--full requires --coupled without --tree or --broad')
    if args.double_polys and (args.full or args.tree or args.broad or not args.coupled):parser.error('--double-polys requires --coupled without other polynomial modes')
    result=broad_search(a) if args.broad else coupled_search(a,args.cent_radius,args.tree,args.public_cosines,args.full,args.double_polys) if args.coupled else analyze(a)
    producer=digest(Path(__file__).read_bytes())
    result.update(profile_sha256=digest(raw),created=time.time(),producer_sha256=producer,tree=args.tree,public_cosines=args.public_cosines,broad=args.broad,full=args.full,double_polys=args.double_polys)
    script_root=Path(__file__).resolve().parent
    producers={name:digest((script_root/name).read_bytes()) for name in
               ('bwe2_lpc_polynomial.py','bwe2_public_math.py','bwe2_float_profile.py','bwe2_blackbox_math.py','bwe2_blackbox.py')}
    result['producer']=producers
    args.out.mkdir(exist_ok=True,parents=True);snapshot=args.out/'producers'/digest(canonical(producers));snapshot.mkdir(parents=True,exist_ok=True)
    for name in producers:
        if not (snapshot/name).exists():atomic(snapshot/name,(script_root/name).read_bytes())
    path=args.out/(digest(canonical(result))+'.json');atomic(path,canonical(result))
    if args.coupled or args.broad:
        matches=[r for r in result['hypotheses'] if r['matches']]
        print(json.dumps(dict(matching_hypotheses=matches,best=sorted(result['hypotheses'],key=lambda r:r['best_max_lpc_error'] if r['best_max_lpc_error'] is not None else float('inf'))[:3])),flush=True)
    else:
        for parity in (0,1):print(json.dumps(sorted([r for r in result['hypotheses'] if r['parity']==parity],key=lambda r:r['squared_error'])[:3]),flush=True)
    print(path)
