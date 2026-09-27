"""Per-channel Decimal reference with explicit mapping and atomic-frame semantics."""
from decimal import Decimal
from sq_oracle import Channel,scaled_channel
from cac_oracle import coupled
from tns_oracle import filtered
from bwe2_oracle import restore
from bwe2_vectors import regions
from sq_math import round_f32

def spectra(element):
    raw=[scaled_channel(c) for c in element['channels']]
    cac=coupled(element) if len(raw)==2 else [list(c) for c in raw]
    tns=filtered(element,cac)
    bwe=[list(c) for c in tns]
    if element['bwe2'] is not None:
        for index,channel in enumerate(element['channels']):
            parameters=element['bwe2']['channels'][index]['parameters'];ics=channel['ics']
            if parameters is None or ics['max_sfb']==0:continue
            region=regions(ics)[0];cutoff=region['target_start_line'];short=ics['block_type']==2
            bwe[index]=list(restore(tuple(tns[index]),short,cutoff,tuple(ics['window_groups']),tuple(parameters['lsf_indices']),tuple(parameters['gain_indices'])))
    return dict(raw=raw,cac=cac,tns=tns,bwe2=bwe)

class Decoder:
    def __init__(self,channels):self.channels=[Channel(strict_transitions=False) for _ in range(channels)]
    def decode(self,truth):
        if truth['inner'] is not None:self.decode(truth['inner'])
        outputs=[None]*len(self.channels)
        for element in truth['elements']:
            current=spectra(element) if element['present'] else None
            for local,global_index in enumerate(element['configuration']['output_channels']):
                state=self.channels[global_index]
                if current is None:
                    outputs[global_index]=[round_f32(v) for v in state.overlap];state.overlap=[Decimal(0)]*1024
                else:outputs[global_index]=state.render(current['bwe2'][local],element['channels'][local]['ics']['block_type'])
        assert all(c is not None for c in outputs)
        return [v for row in zip(*outputs) for v in row]
