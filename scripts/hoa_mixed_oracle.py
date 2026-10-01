"""Decimal mixed recovery from emitted integers, never from candidate reports."""
from decimal import Decimal as D, localcontext
from math import isqrt
import struct
from hoa_salient_oracle import harmonics
from hoa_salient_vectors import format_for
from channel_oracle import spectra
from sq_oracle import Channel
from sq_math import round_f32


def descriptor(spec, previous):
    with localcontext() as ctx:
        ctx.prec=200; n=len(previous); order=isqrt(n)-1; mode=spec['mode'];precision=spec.get('quantization_bits',6)
        out=list(harmonics(spec['azimuth_degrees'],spec['elevation_offset_degrees'],order)) if mode==5 else [D(0)]*n
        for encoded,(k,q) in enumerate(zip(spec['coded_coefficient_indices'],spec['quantized'])):
            value=D(q)/(1<<(precision-1))
            out[k]=previous[k]+(value if spec['signs_positive'][encoded] else -value) if mode==3 else value-1
        if mode==4:
            matrix=[D.from_float(struct.unpack('<f',struct.pack('<I',word))[0]) for word in format_for(order,precision)['modes'][4]['matrices_f32'][spec['cluster']]]
            out=[sum((out[j]*matrix[n*j+k] for j in range(n)),D(0)) for k in range(n)]
        assert all(out[k]==0 for k in spec['ambient_omitted_coefficients'])
        return out


class Decoder:
    def __init__(self, coefficients=16):
        self.history=[[[D(0)]*coefficients for _ in range(4)] for _ in range(5)]
        self.channels=[Channel(strict_transitions=False) for _ in range(coefficients)]
        self.records=[]

    def decode(self, truth):
        if truth['inner'] is not None: self.decode(truth['inner'])
        with localcontext() as ctx:
            ctx.prec=200; side=truth['spatial']['salient']; vectors=[]
            for spec in side['descriptors']:
                sc,sb=spec['component_index'],spec['subband_index']
                v=descriptor(spec,self.history[sc][sb]); self.history[sc][sb]=v; vectors.append(v)
            sources=[spectra(e)['bwe2'][0] if e['present'] else [0.]*1024 for e in truth['elements']]
            scaled=[[0.]*1024 for _ in self.channels]
            for line in range(1024):
                frequency=line%128 if truth['common_window']==2 else line
                band=next(i for i,end in enumerate(side['lines_per_window']) if frequency<end)
                for k in range(len(self.channels)):
                    if k<4: scaled[k][line]=sources[k][line] if sources[k][line] else 0.
                    else:
                        value=sum((D.from_float(sources[4+sc][line])*self.history[sc][band][k] for sc in range(5)),D(0))
                        scaled[k][line]=round_f32(value)
            self.records.append(dict(vectors=[[float(v) for v in row] for row in vectors],scaled=scaled,transport=sources))
            outputs=[state.render(spectrum,truth['common_window']) for state,spectrum in zip(self.channels,scaled)]
            return [v for row in zip(*outputs) for v in row]
