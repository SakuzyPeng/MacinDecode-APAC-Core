#!/usr/bin/env python3
"""Independent actual-size recovery, general mappings and prefix-only outputs."""
import hoa_dynamic_domains_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require
def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value']
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'domain backend/state differs')
    require(impl['hoa_numeric_profile']==vectors.NUMERIC_PROFILE and impl['hoa_dynamic_domains_profile']==vectors.PROFILE,'domain numeric/profile differs')
    require(impl['hoa_dynamic_format_sha256']==impl['hoa_dynamic_domains_format_sha256']==vectors.dynamic_domain_format_sha256(),'domain format identity differs')
    require(impl['hoa_recovery_slot_count']==opts.get('coefficient_count',(opts['order']+1)**2) and impl['hoa_output_coefficient_count']==opts['output_coefficients'],'domain dimensions differ')
    if (opts.get('controls') or {}).get('flag_b'):
        require(impl['hoa_frame_configuration_state_sha256']==vectors.format_binding()['dependencies']['frame_state_v2'],'encoder history identity differs')
    n=opts['output_coefficients']
    if int(n**.5)**2!=n:require('hoa_output_order' not in impl and 'hoa_output_containing_order' in impl,'non-square output marked as a complete order')
if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,
    profile=vectors.PROFILE,frozen_name='hoa-dynamic-domains-vectors-v1.json',metadata_checker=metadata,
    range_kinds=('partial-output','prefix-two','explicit-square-controls')))
