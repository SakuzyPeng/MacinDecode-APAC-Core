"""Decimal per-component interval selection, direct recovery and direct IMDCT."""
from decimal import Decimal as D,localcontext
from hoa_dynamic_oracle import descriptor
from hoa_static_ambient_oracle import ambient_transform
from generate_hoa_static_ambient_tables import SIGNS
from generate_hoa_dynamic_subbands_format import boundaries
from channel_oracle import spectra
from sq_oracle import Channel
from sq_math import round_f32


class Decoder:
    def __init__(self,options):
        self.options=options;self.counts=options.get('counts',[1,3,4,9,16]);self.slots=options.get('coefficient_count',(options.get('order',3)+1)**2)
        self.dimensions=[options.get('coefficient_count',(o+1)**2) for o in options.get('component_orders',[options.get('order',3)]*len(self.counts))]
        self.history=[[[D(0)]*self.dimensions[s] for _ in range(n)] for s,n in enumerate(self.counts)]
        self.channels=[Channel(strict_transitions=False) for _ in range(16 if options.get('dynamic') else self.slots)];self.records=[]
    def decode(self,truth):
        if truth['inner']:self.decode(truth['inner'])
        with localcontext() as ctx:
            ctx.prec=200;side=truth['spatial'];vectors=[];path=self.options.get('path','salient');mixed=path!='salient'
            for spec in side.get('salient',{}).get('descriptors',[]):
                sc,b=spec['component_index'],spec['subband_index'];v=descriptor(dict(spec,quantization_bits=self.options.get('quantization_bits',6)),self.history[sc][b]);self.history[sc][b]=v;vectors.append(v)
            sources=[]
            for e in truth['elements']:
                mapping=e['configuration'].get('transport_channels',e['configuration']['output_channels'])
                sources.extend(spectra(e)['bwe2'] if e['present'] and mapping else [[0.]*1024 for _ in mapping])
            selected=side['ambient_indices'];ambient_data=truth.get('dynamic_selection',{}).get('internal_ambient',{}) if truth.get('dynamic_selection') else side.get('ambient',{})
            index=ambient_data.get('effective_index',3);offset=self.options.get('ambient_count',4 if mixed else 0)
            ambient=ambient_transform(sources[:offset],index) if path=='replace' else []
            exact_ambient=[[D.from_float(sources[r][line]) if index==3 or r>=4 else sum((D.from_float(sources[j][line])*SIGNS[index][j][r]/2 for j in range(4)),D(0)) for line in range(1024)] for r in range(offset)] if path=='add' else []
            short=truth['common_window']==2;grids=[[v//8 if short else v for v in boundaries(n,self.options.get('spatial_method',0))] for n in self.counts]
            internal=[[0.]*1024 for _ in range(self.slots)]
            for line in range(1024):
                frequency=line%128 if short else line;bands=[next(b for b,end in enumerate(grid) if frequency<end) for grid in grids]
                active=[s for s in range(len(self.counts)) if sources[offset+s][line]!=0.]
                for k in range(self.slots):
                    if path=='replace' and k in selected:internal[k][line]=ambient[selected.index(k)][line];continue
                    value=sum((D.from_float(sources[offset+s][line])*self.history[s][bands[s]][k] for s in active if k<self.dimensions[s]),D(0))
                    if path=='add' and k in selected:value+=exact_ambient[selected.index(k)][line]
                    internal[k][line]=round_f32(value)
            dyn=truth.get('dynamic_selection');scaled=internal
            if dyn:
                matrices=[[[int(target==out) for target in row['target_acn_indices']] for out in range(16)] for row in dyn['mappings']];scaled=[[0.]*1024 for _ in range(16)]
                for line in range(1024):
                    frequency=line%128 if short else line;b=next(b for b,end in enumerate(dyn['lines_per_window']) if frequency<end)
                    for out,row in enumerate(matrices[b]):scaled[out][line]=round_f32(sum((D.from_float(internal[j][line])*v for j,v in enumerate(row) if v),D(0)))
            self.records.append(dict(vectors=[[float(v) for v in row] for row in vectors],internal=internal,scaled=scaled,transport=sources))
            out=[state.render(spectrum,truth['common_window']) for state,spectrum in zip(self.channels,scaled)];return [v for row in zip(*out) for v in row]
