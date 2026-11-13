#!/usr/bin/env python3
"""Independent count-dependent recovery, history and three-input range validation."""
import hoa_salient_counts_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require

def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value'];count=len(opts['counts']);dynamic=opts.get('dynamic',False)
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'count backend/state differs')
    require(impl['hoa_numeric_profile']==('apac-hoa-dynamic-selection-math-v1' if dynamic else vectors.NUMERIC_PROFILE),'count recovery profile differs')
    require(impl['hoa_salient_component_count']==count and impl['hoa_salient_count_profile']==vectors.PROFILE,'count metadata differs')
    require(impl['hoa_salient_component_orders']==opts['component_orders'],'component orders differ')

if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,profile=vectors.PROFILE,
    frozen_name='hoa-salient-counts-vectors-v1.json',metadata_checker=metadata,range_kinds=('first-four','replace-twelve','dynamic-nine')))
