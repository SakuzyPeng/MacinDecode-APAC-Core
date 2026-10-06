"""Documented Accelerate vector arithmetic as explicit numerical hypotheses."""
import ctypes as C
import numpy as np
from bwe2_float_profile import SplitComplex


class PublicMath:
    def __init__(self,offset=0):
        if offset not in range(16):raise ValueError('bounded float alignment required')
        self.offset=offset;self.lib=C.CDLL('/System/Library/Frameworks/Accelerate.framework/Accelerate')
        ptr=C.POINTER(C.c_float);stride=C.c_long;length=C.c_ulong
        for name in ('vDSP_zvabs','vDSP_zvmags'):
            fn=getattr(self.lib,name);fn.argtypes=[C.POINTER(SplitComplex),stride,ptr,stride,length];fn.restype=None
        for name in ('vvsqrtf','vvrecf','vvrsqrtf','vvcosf'):
            fn=getattr(self.lib,name);fn.argtypes=[ptr,ptr,C.POINTER(C.c_int)];fn.restype=None
        self.lib.vDSP_svdiv.argtypes=[ptr,ptr,stride,ptr,stride,length];self.lib.vDSP_svdiv.restype=None
        self.lib.vDSP_vdiv.argtypes=[ptr,stride,ptr,stride,ptr,stride,length];self.lib.vDSP_vdiv.restype=None

    @staticmethod
    def ptr(value):return value.ctypes.data_as(C.POINTER(C.c_float))

    def aligned(self,value):
        value=np.asarray(value,np.float32);backing=np.empty(value.size+32,np.float32)
        first=((64-backing.ctypes.data%64)%64)//4+self.offset
        result=backing[first:first+len(value)];result[:]=value;return result

    def unary(self,name,value):
        src=self.aligned(value);dest=self.aligned(np.zeros_like(src));count=C.c_int(len(src))
        getattr(self.lib,name)(self.ptr(dest),self.ptr(src),C.byref(count));return dest.copy()

    def magnitude(self,re,im,kind):
        re,im=self.aligned(re),self.aligned(im)
        if kind in ('zvabs','zvmags-sqrt','zvmags-vvsqrt'):
            dest=self.aligned(np.zeros_like(re));parts=SplitComplex(self.ptr(re),self.ptr(im))
            getattr(self.lib,'vDSP_zvabs' if kind=='zvabs' else 'vDSP_zvmags')(C.byref(parts),1,self.ptr(dest),1,len(dest))
            if kind=='zvabs':return dest.copy()
            energy=dest
        elif kind.startswith('fused'):
            energy=np.float32(re.astype(float)**2+im.astype(float)**2)
        else:energy=np.float32(np.float32(re*re)+np.float32(im*im))
        return self.unary('vvsqrtf',energy) if kind.endswith('vvsqrt') else np.sqrt(energy)

    def divide(self,env,gain,kind):
        env=self.aligned(env);numerator=self.aligned(np.full_like(env,128.));dest=self.aligned(np.zeros_like(env))
        if kind=='svdiv':
            self.lib.vDSP_svdiv(self.ptr(numerator),self.ptr(env),1,self.ptr(dest),1,len(env))
        elif kind=='vdiv':
            self.lib.vDSP_vdiv(self.ptr(env),1,self.ptr(numerator),1,self.ptr(dest),1,len(env))
        elif kind=='reciprocal':dest=np.float32(self.unary('vvrecf',env)*np.float32(128))
        else:dest=np.float32(np.float32(128)/env)
        return np.float32(dest*np.float32(gain))

    def predict(self,a,gain,transform,magnitude='zvabs',division='svdiv'):
        re,im=transform(a);env=self.magnitude(re,im,magnitude)
        if np.any(env<=0) or np.any(~np.isfinite(env)):raise ArithmeticError('invalid modeled magnitude')
        return self.divide(env,gain,division)
