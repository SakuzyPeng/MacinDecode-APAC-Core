"""Diagnostic reconstruction of the bound decoder's variable history layout.

The encoder uses its fixed allocated subband stride and clears every unused
subband. The reference decoder instead indexes active rows with the current
maximum count and only visits those rows, while clearing inactive components
using the allocation stride. This model is not used by production or the
independent mathematical reference.
"""
from decimal import Decimal as D
from hoa_salient_subbands_oracle import Decoder
from hoa_dynamic_oracle import descriptor

RULE='apac-bound-native-active-grid-history-v1'

class NativeHistoryDecoder(Decoder):
    def __init__(self,options):
        super().__init__(options)
        self.capacity_bands=max(self.counts,default=0)
        self.native_history=[D(0)]*(len(self.counts)*self.capacity_bands*self.slots)
    def advance_descriptors(self,truth,opts):
        if not self.frame_config:return super().advance_descriptors(truth,opts)
        counts=opts['counts'];maximum=max(counts,default=0);dims=[opts.get('coefficient_count',(o+1)**2) for o in opts.get('component_orders',[opts['order']]*len(counts))]
        specs=truth['spatial'].get('salient',{}).get('descriptors',[]);lookup={(d['component_index'],d['subband_index']):d for d in specs};decoded={}
        for band in range(maximum):
            for sc,count in enumerate(counts):
                start=(sc*maximum+band)*self.slots
                if band<count:
                    d=lookup[sc,band];v=descriptor(dict(d,quantization_bits=opts.get('quantization_bits',6)),self.native_history[start:start+dims[sc]])
                    decoded[sc,band]=v;value=v+[D(0)]*(self.slots-len(v))
                else:value=[D(0)]*self.slots
                self.native_history[start:start+self.slots]=value
        start=len(counts)*self.capacity_bands*self.slots
        self.native_history[start:]=[D(0)]*(len(self.native_history)-start)
        for sc,component in enumerate(self.history):
            for band in range(len(component)):
                start=(sc*maximum+band)*self.slots
                component[band]=self.native_history[start:start+self.slots] if sc<len(counts) and band<maximum else [D(0)]*self.slots
        return [decoded[d['component_index'],d['subband_index']] for d in specs]
    def history_record(self):
        record=super().history_record()
        if self.frame_config:record.update(native_history=list(map(float,self.native_history)),native_history_rule=RULE)
        return record
