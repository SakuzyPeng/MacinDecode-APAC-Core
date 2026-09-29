"""Decimal associated-Legendre and direct-matrix HOA reference.

Shares only wire dictionaries and physical definitions, not production
trigonometric tables, explicit order-3 polynomials, FFT, or candidate output.
"""
from decimal import Decimal as D, localcontext
from functools import lru_cache
from math import factorial
import struct
from channel_oracle import spectra
from sq_oracle import Channel
from sq_math import sin_pi, cos_pi, round_f32
from hoa_salient_vectors import FORMAT


@lru_cache(maxsize=128)
def harmonics(azimuth,elevation):
    with localcontext() as ctx:
        ctx.prec=200
        z=sin_pi(elevation-90,180);radial=(1-z*z).sqrt();out=[D(0)]*16
        for m in range(4):
            diagonal=D(1)
            for k in range(1,m+1):diagonal*=D(2*k-1)*radial
            previous=None;current=diagonal
            for degree in range(m,4):
                if degree>m:
                    next_value=(D(2*degree-1)*z*current-(D(degree+m-1)*previous if previous is not None else D(0)))/D(degree-m)
                    previous,current=current,next_value
                scale=(D(2*degree+1)*D(2 if m else 1)*D(factorial(degree-m))/D(factorial(degree+m))).sqrt()/4
                center=degree*(degree+1)
                out[center+m]=current*scale*cos_pi(m*azimuth,180)
                if m:out[center-m]=-current*scale*sin_pi(m*azimuth,180)
        return tuple(out)


def descriptor(spec,previous):
    with localcontext() as ctx:
        ctx.prec=200
        mode=spec['mode'];out=list(harmonics(spec['azimuth_degrees'],spec['elevation_offset_degrees'])) if mode==5 else [D(0)]*16
        for i,q in enumerate(spec['quantized']):
            value=D(q)/32
            out[i]=previous[i]+(value if spec['signs_positive'][i] else -value) if mode==3 else value-1
        if mode==4:
            matrix=[D.from_float(struct.unpack('<f',struct.pack('<I',word))[0]) for word in FORMAT['modes'][4]['matrices_f32'][spec['cluster']]]
            out=[sum((out[j]*matrix[16*j+k] for j in range(16)),D(0)) for k in range(16)]
        return out


class Decoder:
    def __init__(self):
        self.history=[[[D(0)]*16 for _ in range(4)] for _ in range(5)]
        self.channels=[Channel(strict_transitions=False) for _ in range(16)]
        self.records=[]

    def decode(self,truth):
        if truth['inner'] is not None:self.decode(truth['inner'])
        with localcontext() as ctx:
            ctx.prec=200
            side=truth['spatial']['salient'];vectors=[]
            for spec in side['descriptors']:
                sc,sb=spec['component_index'],spec['subband_index'];v=descriptor(spec,self.history[sc][sb]);self.history[sc][sb]=v;vectors.append(v)
            sources=[spectra(e)['bwe2'][0] if e['present'] else [0.]*1024 for e in truth['elements'][:5]]
            scaled=[[0.]*1024 for _ in range(16)]
            for line in range(1024):
                frequency=line%128 if truth['common_window']==2 else line
                band=next(i for i,end in enumerate(side['lines_per_window']) if frequency<end)
                for k in range(16):
                    value=sum((D.from_float(source[line])*self.history[sc][band][k] for sc,source in enumerate(sources)),D(0))
                    scaled[k][line]=round_f32(value)
            self.records.append(dict(vectors=[[float(v) for v in row] for row in vectors],scaled=scaled))
            outputs=[state.render(spectrum,truth['common_window']) for state,spectrum in zip(self.channels,scaled)]
            return [v for row in zip(*outputs) for v in row]
