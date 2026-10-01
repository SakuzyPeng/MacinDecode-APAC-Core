"""Decimal per-component interval selection, direct recovery and direct IMDCT."""
from decimal import Decimal as D,localcontext
from hoa_dynamic_oracle import descriptor
from hoa_static_ambient_oracle import ambient_transform
from generate_hoa_static_ambient_tables import SIGNS
from generate_hoa_dynamic_subbands_format import boundaries
from channel_oracle import spectra
from sq_oracle import Channel
from sq_math import round_f32
import json,struct
from pathlib import Path


class Decoder:
    def __init__(self,options):
        self.options=options;self.counts=options.get('counts',[1,3,4,9,16]);self.slots=options.get('coefficient_count',(options.get('order',3)+1)**2)
        self.dimensions=[options.get('coefficient_count',(o+1)**2) for o in options.get('component_orders',[options.get('order',3)]*len(self.counts))]
        self.frame_config=options.get('controls',{}).get('flag_b',False)
        self.history=[[[D(0)]*(self.slots if self.frame_config else self.dimensions[s]) for _ in range(max(self.counts) if self.frame_config else n)] for s,n in enumerate(self.counts)]
        self.channels=[Channel(strict_transitions=False) for _ in range(options.get('output_coefficients',16 if options.get('dynamic') else self.slots))];self.records=[]
    def advance_descriptors(self,truth,opts):
        counts=opts.get('counts',self.counts);dimensions=[opts.get('coefficient_count',(o+1)**2) for o in opts.get('component_orders',[opts.get('order',3)]*len(counts))];vectors=[]
        for spec in truth['spatial'].get('salient',{}).get('descriptors',[]):
            sc,b=spec['component_index'],spec['subband_index'];v=descriptor(dict(spec,quantization_bits=opts.get('quantization_bits',6)),self.history[sc][b][:dimensions[sc]])
            self.history[sc][b]=v+[D(0)]*(self.slots-len(v)) if self.frame_config else v;vectors.append(v)
        if self.frame_config:
            for sc,component in enumerate(self.history):
                for band in component[counts[sc] if sc<len(counts) else 0:]:band[:]=[D(0)]*len(band)
        return vectors
    def history_record(self):
        return {'history':[[list(map(float,band)) for band in component] for component in self.history]}
    def decode(self,truth):
        if truth['inner']:self.decode(truth['inner'])
        with localcontext() as ctx:
            ctx.prec=200;side=truth['spatial'];vectors=[];opts=truth.get('frame_options',self.options);path=opts.get('path','salient')
            counts=opts.get('counts',self.counts);dimensions=[opts.get('coefficient_count',(o+1)**2) for o in opts.get('component_orders',[opts.get('order',3)]*len(counts))]
            offset=opts.get('ambient_count',4 if path!='salient' else 0);addition=bool(path=='add' and counts and offset)
            control=opts.get('controls') or {};rounded=control.get('flag_f',True)
            means=[D(0)]*self.slots
            if not control.get('flag_a',True):
                words=json.loads((Path(__file__).resolve().parents[1]/'data/hoa-spatial-controls-format-v1.json').read_text())['mean_coefficients_f32']
                means=[D.from_float(struct.unpack('<f',struct.pack('<I',word))[0]) for word in words[:self.slots]]
            vectors=self.advance_descriptors(truth,opts)
            sources=[]
            for e in truth['elements']:
                mapping=e['configuration'].get('transport_channels',e['configuration']['output_channels'])
                sources.extend(spectra(e)['bwe2'] if e['present'] and mapping else [[0.]*1024 for _ in mapping])
            selected=side['ambient_indices'];ambient_data=truth.get('dynamic_selection',{}).get('internal_ambient',{}) if truth.get('dynamic_selection') else side.get('ambient',{})
            index=ambient_data.get('effective_index',3)
            ambient=ambient_transform(sources[:offset],index) if not addition else []
            exact_ambient=[[D.from_float(sources[r][line]) if index==3 or r>=4 else sum((D.from_float(sources[j][line])*SIGNS[index][j][r]/2 for j in range(4)),D(0)) for line in range(1024)] for r in range(offset)] if addition else []
            short=truth['common_window']==2;grids=[[v//8 if short and rounded else v for v in boundaries(n,opts.get('spatial_method',0),rounded)] for n in counts]
            internal=[[0.]*1024 for _ in range(self.slots)]
            for line in range(1024):
                frequency=(line%128)*8+line//128 if short and not rounded else line%128 if short else line;bands=[next(b for b,end in enumerate(grid) if frequency<end) for grid in grids]
                active=[s for s in range(len(counts)) if sources[offset+s][line]!=0.]
                for k in range(self.slots):
                    if not addition and k in selected:internal[k][line]=ambient[selected.index(k)][line];continue
                    value=means[k]+sum((D.from_float(sources[offset+s][line])*self.history[s][bands[s]][k] for s in active if k<dimensions[s]),D(0))
                    if addition and k in selected:value+=exact_ambient[selected.index(k)][line]
                    internal[k][line]=round_f32(value)
            dyn=truth.get('dynamic_selection');scaled=internal
            if dyn:
                outputs=len(self.channels)
                if self.slots>=outputs:scaled=internal[:outputs]
                else:
                    matrices=[[[int(target==out) for target in row['target_acn_indices']] for out in range(outputs)] for row in dyn['mappings']];scaled=[[0.]*1024 for _ in range(outputs)]
                    for line in range(1024):
                        frequency=(line%128)*8+line//128 if short and not rounded else line%128 if short else line;b=next(b for b,end in enumerate(dyn['lines_per_window'] if rounded else dyn['subband_ends']) if frequency<end)
                        for out,row in enumerate(matrices[b]):scaled[out][line]=round_f32(sum((D.from_float(internal[j][line])*v for j,v in enumerate(row) if v),D(0)))
            self.records.append(dict(vectors=[[float(v) for v in row] for row in vectors],internal=internal,scaled=scaled,transport=sources,
                                     **(self.history_record() if opts.get('controls') is not None else {})))
            out=[state.render(spectrum,truth['common_window']) for state,spectrum in zip(self.channels,scaled)];return [v for row in zip(*out) for v in row]
