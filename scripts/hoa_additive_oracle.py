"""Decimal direct additive formula; no compensated algorithm or candidate-derived truth."""
from decimal import Decimal as D, localcontext
from hoa_mixed_oracle import descriptor
from generate_hoa_static_ambient_tables import SIGNS
from channel_oracle import spectra
from sq_oracle import Channel
from sq_math import round_f32


class Decoder:
    def __init__(self, slots, channels):
        self.history=[[[D(0)]*slots for _ in range(4)] for _ in range(5)]
        self.channels=[Channel(strict_transitions=False) for _ in range(channels)]
        self.slots=slots; self.records=[]

    def decode(self, truth):
        if truth['inner'] is not None: self.decode(truth['inner'])
        with localcontext() as ctx:
            ctx.prec=200; side=truth['spatial']['salient']; vectors=[]
            for spec in side['descriptors']:
                sc,sb=spec['component_index'],spec['subband_index']
                v=descriptor(spec,self.history[sc][sb]); self.history[sc][sb]=v; vectors.append(v)
            sources=[spectra(e)['bwe2'][0] if e['present'] else [0.]*1024 for e in truth['elements']]
            selection=truth['additive']['selection']; index=truth['additive']['effective_transform_index']
            ambient=[[D.from_float(sources[r][line]) if index==3 else sum((D.from_float(sources[j][line])*SIGNS[index][j][r]/2 for j in range(4)),D(0)) for line in range(1024)] for r in range(4)]
            internal=[[0.]*1024 for _ in range(self.slots)]
            for line in range(1024):
                frequency=line%128 if truth['common_window']==2 else line
                band=next(b for b,end in enumerate(side['lines_per_window']) if frequency<end)
                for slot in range(self.slots):
                    value=sum((D.from_float(sources[4+s][line])*self.history[s][band][slot] for s in range(5)),D(0))
                    if slot in selection:
                        r=selection.index(slot)
                        value+=ambient[r][line]
                    internal[slot][line]=round_f32(value)
            dyn=truth['dynamic_selection']
            if dyn:
                matrices=[[[int(target==out) for target in row['target_acn_indices']] for out in range(16)] for row in dyn['mappings']]
                scaled=[[0.]*1024 for _ in self.channels]
                for line in range(1024):
                    frequency=line%128 if truth['common_window']==2 else line
                    band=next(b for b,end in enumerate(dyn['lines_per_window']) if frequency<end)
                    for out,row in enumerate(matrices[band]):
                        scaled[out][line]=round_f32(sum((D.from_float(internal[i][line])*weight for i,weight in enumerate(row) if weight),D(0)))
            else: scaled=internal
            self.records.append(dict(vectors=[[float(v) for v in row] for row in vectors],internal=internal,ambient=[[float(v) for v in row] for row in ambient],scaled=scaled,transport=sources))
            outputs=[state.render(spectrum,truth['common_window']) for state,spectrum in zip(self.channels,scaled)]
            return [v for row in zip(*outputs) for v in row]
