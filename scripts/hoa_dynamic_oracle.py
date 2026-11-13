"""Decimal nine-slot recovery and independent one-hot output selection matrices."""
from decimal import Decimal as D,localcontext
from hoa_mixed_oracle import descriptor as compact_descriptor
from hoa_static_ambient_oracle import ambient_transform
from channel_oracle import spectra
from sq_oracle import Channel
from sq_math import round_f32


def descriptor(spec,previous):
    spec=dict(spec)
    spec.setdefault('coded_coefficient_indices',list(range(len(spec['quantized']))))
    spec.setdefault('ambient_omitted_coefficients',[])
    return compact_descriptor(spec,previous)


class Decoder:
    def __init__(self):
        self.history=[[[D(0)]*9 for _ in range(4)] for _ in range(5)]
        self.channels=[Channel(strict_transitions=False) for _ in range(16)]; self.records=[]
    def decode(self,truth):
        if truth['inner'] is not None: self.decode(truth['inner'])
        with localcontext() as ctx:
            ctx.prec=200; side=truth['spatial']['salient']; dynamic=truth['dynamic_selection']; vectors=[]
            for spec in side['descriptors']:
                sc,sb=spec['component_index'],spec['subband_index']; v=descriptor(spec,self.history[sc][sb])
                self.history[sc][sb]=v; vectors.append(v)
            sources=[spectra(e)['bwe2'][0] if e['present'] else [0.]*1024 for e in truth['elements']]
            selected=dynamic['ambient_recovery_slots']; offset=len(selected)
            index=dynamic.get('internal_ambient',{}).get('effective_index',3)
            ambient=ambient_transform(sources[:offset],index) if offset else []
            internal=[[0.]*1024 for _ in range(9)]
            for line in range(1024):
                frequency=line%128 if truth['common_window']==2 else line
                band=next(i for i,end in enumerate(side['lines_per_window']) if frequency<end)
                for slot in range(9):
                    if slot in selected: internal[slot][line]=ambient[selected.index(slot)][line]
                    else: internal[slot][line]=round_f32(sum((D.from_float(sources[offset+sc][line])*self.history[sc][band][slot] for sc in range(5)),D(0)))
            matrices=[[[int(target==out) for target in row['target_acn_indices']] for out in range(16)] for row in dynamic['mappings']]
            assert all(all(sum(row)<=1 for row in matrix) and all(sum(row[i] for row in matrix)==1 for i in range(9)) for matrix in matrices)
            scaled=[[0.]*1024 for _ in range(16)]
            for line in range(1024):
                frequency=line%128 if truth['common_window']==2 else line
                band=next(i for i,end in enumerate(dynamic['lines_per_window']) if frequency<end)
                for out,row in enumerate(matrices[band]):
                    scaled[out][line]=round_f32(sum((D.from_float(internal[i][line])*weight for i,weight in enumerate(row) if weight),D(0)))
            self.records.append(dict(vectors=[[float(v) for v in row] for row in vectors],internal=internal,ambient=ambient,scaled=scaled,transport=sources))
            outputs=[state.render(spectrum,truth['common_window']) for state,spectrum in zip(self.channels,scaled)]
            return [v for row in zip(*outputs) for v in row]
