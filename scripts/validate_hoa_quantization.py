#!/usr/bin/env python3
"""Independent seven/eight/nine-bit descriptor and PCM validation."""
import hoa_quantization_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require

def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value'];dynamic=opts.get('dynamic',False)
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'quantization backend/state differs')
    require(impl['hoa_numeric_profile']==('apac-hoa-dynamic-selection-math-v1' if dynamic else vectors.NUMERIC_PROFILE),'quantization recovery differs')
    require(impl['hoa_salient_quantization_bits']==opts['quantization_bits'] and impl['hoa_salient_quantization_profile']==vectors.PROFILE,'quantization metadata differs')

if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,profile=vectors.PROFILE,
    frozen_name='hoa-quantization-vectors-v1.json',metadata_checker=metadata,range_kinds=('q7-first','q8-mixed','q9-dynamic')))
