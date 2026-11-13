#!/usr/bin/env python3
"""Independent full-order harmonic, matrix, compensated recovery and PCM validation."""
import hoa_expanded_orders_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require

def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value']
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'expanded-order backend/state differs')
    require(impl['hoa_numeric_profile']==vectors.NUMERIC_PROFILE and impl['hoa_expanded_orders_profile']==vectors.PROFILE,'expanded-order recovery differs')
    require(impl['hoa_expanded_math_sha256']==vectors.format_binding()['dependencies']['expanded_math'],'normalization identity differs')
    if opts['profile']!=5 or opts['level']!=0:require((impl['hoa_profile_id'],impl['hoa_level_id'])==(opts['profile'],opts['level']),'profile/level provenance differs')

if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,profile=vectors.PROFILE,
    frozen_name='hoa-expanded-orders-vectors-v1.json',metadata_checker=metadata,range_kinds=('zero','fourth','tenth')))
