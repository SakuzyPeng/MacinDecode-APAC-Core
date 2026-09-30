"""Decimal direct-matrix ambient recovery, independent of production compensation."""
from decimal import Decimal as D, localcontext
from hoa_mixed_oracle import descriptor
from generate_hoa_static_ambient_tables import SIGNS
from channel_oracle import spectra
from sq_oracle import Channel
from sq_math import round_f32


def ambient_transform(sources,index):
    with localcontext() as ctx:
        ctx.prec=200
        return [list(source) for source in sources] if index==3 else [
            [round_f32(sum((D.from_float(sources[j][line])*SIGNS[index][j][k]/2 for j in range(4)),D(0))) for line in range(1024)]
            if k<4 else list(sources[k]) for k in range(len(sources))]


class Decoder:
    def __init__(self,coefficients):
        self.history=[[[D(0)]*coefficients for _ in range(4)] for _ in range(5)]
        self.channels=[Channel(strict_transitions=False) for _ in range(coefficients)]; self.records=[]
    def decode(self,truth):
        if truth['inner'] is not None: self.decode(truth['inner'])
        with localcontext() as ctx:
            ctx.prec=200; side=truth['spatial']; salient=side.get('salient'); vectors=[]
            if salient:
                for spec in salient['descriptors']:
                    sc,sb=spec['component_index'],spec['subband_index']; v=descriptor(spec,self.history[sc][sb])
                    self.history[sc][sb]=v; vectors.append(v)
            sources=[spectra(e)['bwe2'][0] if e['present'] else [0.]*1024 for e in truth['elements']]
            selection=side['ambient']['selection']; ambient=ambient_transform(sources[:len(selection)],side['ambient']['effective_index'])
            scaled=[[0.]*1024 for _ in self.channels]
            for line in range(1024):
                if salient:
                    frequency=line%128 if truth['common_window']==2 else line
                    band=next(i for i,end in enumerate(salient['lines_per_window']) if frequency<end)
                    for k in range(len(self.channels)):
                        if k not in selection:
                            scaled[k][line]=round_f32(sum((D.from_float(sources[4+sc][line])*self.history[sc][band][k] for sc in range(5)),D(0)))
                for slot,k in enumerate(selection): scaled[k][line]=ambient[slot][line] if ambient[slot][line] else 0.
            self.records.append(dict(vectors=[[float(v) for v in row] for row in vectors],ambient=ambient,scaled=scaled,transport=sources))
            outputs=[state.render(spectrum,truth['common_window']) for state,spectrum in zip(self.channels,scaled)]
            return [v for row in zip(*outputs) for v in row]
