#!/usr/bin/env python3
"""Independent actual-dimension recovery, state and container-range validation."""
import hoa_partial_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require

def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value']
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'partial backend/state differs')
    require(impl['hoa_numeric_profile']==vectors.NUMERIC_PROFILE and impl['hoa_partial_domain_profile']==vectors.PROFILE,'partial math/profile differs')
    require(impl['hoa_full_order'] is False and impl['hoa_recovery_slot_count']==opts['coefficient_count'],'actual dimension metadata differs')

if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,
    profile=vectors.PROFILE,frozen_name='hoa-partial-vectors-v1.json',metadata_checker=metadata,
    range_kinds=('two','five-replace','eight-grids-drc')))
