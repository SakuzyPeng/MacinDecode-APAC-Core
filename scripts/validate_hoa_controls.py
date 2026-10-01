#!/usr/bin/env python3
"""Independent control semantics, compact active shapes and range decoding."""
import hoa_controls_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require

def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value']
    require(decoded['backend']==(vectors.BACKEND.replace('_v1','_v2') if opts.get('controls',{}).get('flag_b') else vectors.BACKEND) and decoded['packet_state_profile']==(vectors.STATE_PROFILE.replace('-v1','-v2') if opts.get('controls',{}).get('flag_b') else vectors.STATE_PROFILE),'control backend/state differs')
    require(impl['hoa_spatial_controls_profile']==vectors.PROFILE and impl['hoa_spatial_controls_format_sha256']==vectors.control_format_sha256(),'control identity differs')
    require(impl['hoa_spatial_controls']==vectors.control_values(opts.get('controls'),opts['path']),'control flags differ')
    if opts.get('controls',{}).get('flag_b'):
        require(impl['hoa_frame_configuration_state_sha256']==vectors.format_binding()['dependencies']['frame_state_v2'],'encoder history identity differs')

if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,
    profile=vectors.PROFILE,frozen_name='hoa-controls-vectors-v2.json',metadata_checker=metadata,
    range_kinds=('mean','frame-variable','combined-partial')))
