"""Component-wise high-precision reference and explicit bounded source routing."""
from hoa_salient_subbands_oracle import Decoder as SpatialDecoder
from hoa_source_layout_oracle import Decoder as SourceDecoder
from hoa_remapping_oracle import Decoder as RemappingDecoder
from channel_oracle import Decoder as ChannelDecoder
from hoa_shared_vectors import parts,component_options,effective_components

class Decoder:
    def __init__(self,options):
        self.options=options;self.rate=options.get('rate',48000);self.components=options['components'];self.decoders=[]
        self.channels=options.get('total_channels') or sum(parts(c,self.rate)['channels'] for c in effective_components(self.components,self.rate))
        for c in self.components:
            opts=component_options(c,self.rate);n=parts(c,self.rate)['channels'];opts.setdefault('output_coefficients',n)
            if c.get('type',2)==0:d=ChannelDecoder(n,self.rate)
            else:d=(RemappingDecoder if opts.get('remapping') is not None else SourceDecoder if opts.get('source_layout') else SpatialDecoder)(opts)
            self.decoders.append(d)
    def decode(self,truth):
        if truth['inner']:self.decode(truth['inner'])
        decoded=[]
        for component,decoder,t in zip(self.components,self.decoders,truth['components']):
            n=parts(component,self.rate)['channels'];pcm=decoder.decode(t)
            decoded.append([pcm[i::n] for i in range(n)])
        if self.options.get('scene'):
            raw=[c for group in decoded for c in group];output=[None]*self.channels;descriptors=self.options.get('additional')
            if descriptors is not None:
                starts=[d['start'] for d in descriptors]
                spans=[(start,self.channels if len(starts)==1 else min([s for s in starts if s>start]+[self.channels])-start) for start in starts]
            else:
                spans=[];cursor=0
                for c,group in zip(self.components,decoded):
                    spans.append((c.get('start',cursor),len(group)));cursor+=len(group)
            source=0
            for start,count in spans:
                output[start:start+count]=raw[source:source+count];source+=count
            assert all(c is not None for c in output)
        else:
            output=[]
            for group in decoded:
                if len(output)+len(group)<=self.channels:output.extend(group)
        assert len(output)==self.channels
        return [sample for frame in zip(*output) for sample in frame]
