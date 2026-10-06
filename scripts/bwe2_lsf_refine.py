"""Fixed-gain LSF fitting and clipped-sum analysis, independent of old books."""
import numpy as np


def log_envelope_jacobian(frequencies, bins):
    frequencies=np.asarray(frequencies,dtype=float)
    omega=np.pi*np.asarray(bins,dtype=float)/512
    angles=np.pi*frequencies/12000
    factors=2*(np.cos(omega)[:,None]-np.cos(angles)[None,:])
    p=np.prod(factors[:,::2],axis=1)
    q=np.prod(factors[:,1::2],axis=1)
    c2=np.cos(omega/2)**2;s2=np.sin(omega/2)**2
    power=p*p*c2+q*q*s2
    if np.any(power<=0):raise ArithmeticError('nonpositive factored envelope')
    jacobian=np.empty((len(bins),16))
    for j in range(16):
        siblings=[k for k in range(j%2,16,2) if k!=j]
        derivative=np.prod(factors[:,siblings],axis=1)*2*np.sin(angles[j])*np.pi/12000
        jacobian[:,j]=(p*c2 if j%2==0 else q*s2)*derivative/power
    return .5*np.log(power),jacobian


def fit_fixed_gain(spectrum,carrier,initial,gain,cutoff=256,spacing=1.,max_steps=160):
    bins=np.arange(1,512,2)
    transfer=spectrum[cutoff+bins]/carrier[128+bins%(cutoff-128)]
    if np.any(transfer<=0):raise ArithmeticError('nonpositive measured transfer')
    target=np.log(gain/transfer)
    x=np.asarray(initial,dtype=float).copy()
    # Fitting is performed both freely and with the known minimum spacing.
    # The unconstrained fit is diagnostic, never an original-book candidate.
    x[0]=max(x[0],spacing)
    for i in range(1,16):x[i]=max(x[i],x[i-1]+spacing)
    if x[-1]>=12000-spacing:
        x[-1]=12000-spacing
        for i in range(14,-1,-1):x[i]=min(x[i],x[i+1]-spacing)
    damping=1e-3
    history=[]
    for iteration in range(max_steps):
        predicted,j=log_envelope_jacobian(x,bins)
        residual=predicted-target
        loss=float(residual@residual)
        if not np.isfinite(loss):raise ArithmeticError('nonfinite fit')
        scale=np.maximum(np.linalg.norm(j,axis=0),1e-12)
        step=np.linalg.lstsq(np.vstack([j,np.diag(np.sqrt(damping)*scale)]),
                             np.r_[-residual,np.zeros(16)],rcond=None)[0]
        if np.max(abs(step))>100:step*=100/np.max(abs(step))
        accepted=False
        for factor in (1.,.5,.25,.125,.0625,.03125,.015625,.0078125):
            trial=x+step*factor
            if trial[0]<spacing or trial[-1]>12000-spacing or np.any(np.diff(trial)<spacing):continue
            value,_=log_envelope_jacobian(trial,bins)
            trial_loss=float((value-target)@(value-target))
            if trial_loss<loss:
                x=trial;damping=max(damping/3,1e-12);accepted=True
                history.append(dict(iteration=iteration,loss=trial_loss,max_step=float(np.max(abs(step*factor)))))
                break
        if not accepted:
            damping*=10
            if damping>1e10:break
        elif np.max(abs(step*factor))<1e-7:break
    predicted,j=log_envelope_jacobian(x,bins)
    residual=predicted-target
    singular=np.linalg.svd(j,compute_uv=False)
    return dict(lsf=x.tolist(),log_rms=float(np.sqrt(np.mean(residual**2))),
        relative_transfer_rms=float(np.sqrt(np.mean(np.expm1(residual)**2))),
        max_relative_transfer_error=float(np.max(abs(np.expm1(residual)))),
        jacobian_condition=float(singular[0]/singular[-1]),iterations=len(history),history=history,
        spacing_constraint=spacing)


def condition(values):
    result=np.array(values,dtype=float).copy()
    for k in range(16):result[k]=max(result[k],(result[k-1] if k else 0)+50)
    if result[-1]>11950:
        for k in range(15,-1,-1):result[k]=min(result[k],(result[k+1] if k<15 else 12000)-50)
    return result


def condition_jacobian(values):
    result=np.array(values,dtype=float).copy();jac=np.eye(16)
    for k in range(16):
        bound=(result[k-1] if k else 0)+50
        if result[k]<bound:
            result[k]=bound;jac[k]=jac[k-1] if k else 0
    if result[-1]>11950:
        for k in range(15,-1,-1):
            bound=(result[k+1] if k<15 else 12000)-50
            if result[k]>bound:
                result[k]=bound;jac[k]=jac[k+1] if k<15 else 0
    return result,jac


def joint_envelope_fit(model,observations,gain,max_steps=40):
    """Fit both relative tables to envelopes, retaining the explicit offset gauge."""
    first_indices=model['first_indices'];second_indices=model['second_indices']
    na,nb=len(first_indices),len(second_indices);size=(na+nb-1)*16
    initial=np.vstack([np.asarray(model['first'])[1:],model['second']]).reshape(-1)
    prepared=[]
    bins=np.arange(1,512,2)
    for row in observations:
        a=first_indices.index(row['pair'][0]);b=second_indices.index(row['pair'][1])
        positions=[]
        if a:positions.append(np.arange((a-1)*16,a*16))
        positions.append(np.arange((na-1+b)*16,(na+b)*16))
        prepared.append((positions,np.asarray(row['log_envelope']),row['pair']))
    def evaluate(x,derivatives=False):
        loss=0.;h=np.zeros((size,size));g=np.zeros(size);errors=[]
        for positions,target,pair in prepared:
            raw=sum((x[p] for p in positions),np.zeros(16))
            f,cj=condition_jacobian(raw)
            prediction,j=log_envelope_jacobian(f,bins)
            residual=prediction-target;loss+=float(residual@residual)
            errors.append(dict(pair=pair,relative_transfer_rms=float(np.sqrt(np.mean(np.expm1(-residual)**2)))))
            if derivatives:
                j=j@cj;block=j.T@j;gradient=j.T@residual
                for p in positions:
                    g[p]+=gradient
                    for q in positions:h[np.ix_(p,q)]+=block
        return loss,h,g,errors
    x=initial.copy();damping=1e-3;history=[]
    for iteration in range(max_steps):
        loss,h,g,_=evaluate(x,True)
        scale=np.sqrt(np.maximum(np.diag(h),1e-20))
        step=np.linalg.solve(h/scale[:,None]/scale[None,:]+damping*np.eye(size),-g/scale)/scale
        if np.max(abs(step))>5:step*=5/np.max(abs(step))
        accepted=False
        for fraction in (1.,.5,.25,.125,.0625,.03125):
            trial=x+step*fraction;new_loss,_,_,_=evaluate(trial)
            if new_loss<loss:
                x=trial;damping=max(damping/3,1e-12);accepted=True
                history.append(dict(iteration=iteration,loss=new_loss,max_step=float(np.max(abs(step*fraction)))))
                break
        if not accepted:
            damping*=10
            if damping>1e10:break
        elif np.max(abs(step*fraction))<1e-6:break
    loss,_,_,errors=evaluate(x)
    first=np.vstack([np.zeros((1,16)),x[:(na-1)*16].reshape(na-1,16)])
    second=x[(na-1)*16:].reshape(nb,16)
    return dict(first_indices=first_indices,second_indices=second_indices,first=first.tolist(),second=second.tolist(),
        observations=len(observations),log_rms=float(np.sqrt(loss/(len(observations)*256))),errors=errors,history=history,
        gain=gain,original_tables_identified=False,eligible_lsf=False)


def relative_factorization(records,margin=5.):
    first_indices=sorted({r['pair'][0] for r in records});second_indices=sorted({r['pair'][1] for r in records})
    na,nb=len(first_indices),len(second_indices)
    first_positions={v:i for i,v in enumerate(first_indices)};second_positions={v:i for i,v in enumerate(second_indices)}
    first=np.zeros((na,16));second=np.zeros((nb,16));dimensions=[]
    for k in range(16):
        matrix=[];target=[];used=[]
        for row in records:
            f=np.array(row['lsf'])
            if f[k] <= (f[k-1] if k else 0)+50+margin or f[-1]>=11950-margin:continue
            a,b=first_positions[row['pair'][0]],second_positions[row['pair'][1]]
            equation=np.zeros(na+nb-1)
            # A[0]=0 fixes a computational gauge only, not the original table.
            if a:equation[a-1]=1
            equation[na-1+b]=1
            matrix.append(equation);target.append(f[k]);used.append(row['pair'])
        if not matrix:dimensions.append(dict(index=k,identified=False,edges=0));continue
        matrix=np.array(matrix);target=np.array(target)
        answer,_,rank,_=np.linalg.lstsq(matrix,target,rcond=None)
        first[1:,k]=answer[:na-1];second[:,k]=answer[na-1:]
        dimensions.append(dict(index=k,identified=bool(rank==na+nb-1),edges=len(target),rank=int(rank),
            max_residual=float(np.max(abs(matrix@answer-target))),used_pairs=used))
    predictions=[]
    for row in records:
        a,b=first_positions[row['pair'][0]],second_positions[row['pair'][1]]
        predicted=condition(first[a]+second[b])
        predictions.append(dict(pair=row['pair'],max_frequency_error=float(np.max(abs(predicted-row['lsf'])))))
    return dict(first_indices=first_indices,second_indices=second_indices,dimensions=dimensions,first=first.tolist(),second=second.tolist(),
        predictions=predictions,original_tables_identified=False,
        qualification='diagnostic relative representation with arbitrary A[0]=0; not an eligible source replacement')
