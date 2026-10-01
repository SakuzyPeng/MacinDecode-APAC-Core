#!/usr/bin/env python3
"""Independent ambient-count recovery with sparse output and unused-carrier checks."""
import hoa_ambient_counts_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require

def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value'];dynamic=opts.get('dynamic',False)
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'ambient-count backend/state differs')
    require(impl['hoa_numeric_profile']==('apac-hoa-dynamic-selection-math-v1' if dynamic else vectors.NUMERIC_PROFILE),'ambient-count recovery differs')
    require(impl['hoa_ambient_component_count']==opts['ambient_count'] and impl['hoa_ambient_count_profile']==vectors.PROFILE,'ambient-count metadata differs')

if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,profile=vectors.PROFILE,
    frozen_name='hoa-ambient-counts-vectors-v1.json',metadata_checker=metadata,range_kinds=('five','pure-selected','dynamic-three')))
