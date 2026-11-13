#!/usr/bin/env python3
"""Six salient partition representatives: independent Decimal math or exact build replay."""
import hoa_salient_partition_vectors as vectors
from generate_hoa_salient_partition_format import PROFILE,generate
from validate_hoa_salient_subbands import main
if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=generate,profile=PROFILE,frozen_name='hoa-partition-vectors-v1.json',format_name='hoa-salient-subbands-format-v2.json'))
