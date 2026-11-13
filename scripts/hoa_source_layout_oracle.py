"""Decimal source matrices and source-index placement, followed by direct IMDCT."""
import struct
from decimal import Decimal as D
from hoa_salient_subbands_oracle import Decoder as SpatialDecoder
from hoa_source_layout_vectors import FORMAT
from sq_math import round_f32


class Decoder(SpatialDecoder):
    def source_layout(self,internal,truth,opts):
        source=opts['source_layout'];n=opts['output_coefficients'];m=len(internal)
        parameter=opts.get('controls',{}).get('parameter_0',1)
        entry=next((e for e in FORMAT['layouts'] if e['tag']==source['tag']),None)
        lfe=entry['lfe_indices'] if entry else []
        result=[[0.]*1024 for _ in range(max(m,n))]
        if parameter==0:
            matrix=[D.from_float(struct.unpack('<f',struct.pack('<I',word))[0]) for word in FORMAT['matrices'][entry['matrix_id']]]
            rows=[k for k in range(n) if k not in lfe]
            for row,channel in enumerate(rows):
                for line in range(1024):
                    result[channel][line]=round_f32(sum((matrix[row*m+i]*D.from_float(internal[i][line]) for i in range(m)),D(0)))
        else:
            for channel in range(m):
                if parameter==1 and entry and (channel in lfe or channel>=n):continue
                result[channel]=list(internal[channel])
        return result
