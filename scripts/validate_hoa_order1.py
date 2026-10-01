#!/usr/bin/env python3
"""First-order descriptor mathematics, zero-width descriptions and portable fingerprints."""
import hoa_order1_vectors as vectors
from hoa_component_orders_vectors import PROFILE as COMPONENT_PROFILE,STATE_PROFILE,BACKEND
from generate_hoa_salient_subbands_format import generate as perceptual,PROFILE as SUBBAND_PROFILE
from generate_hoa_salient_partition_format import generate as partitions,PROFILE as PARTITION_PROFILE
from validate_hoa_salient_subbands import main
from validate import require


def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value'];counts=list(opts['counts']);method=opts.get('spatial_method',0);dynamic=opts.get('dynamic',False)
    require(decoded['backend']==BACKEND and decoded['packet_state_profile']==STATE_PROFILE,'component backend/state differs')
    require(impl['hoa_numeric_profile']==('apac-hoa-dynamic-selection-math-v1' if dynamic else COMPONENT_PROFILE) and impl['hoa_descriptor_numeric_profile']==COMPONENT_PROFILE,'numeric profile differs')
    if dynamic:require(impl['hoa_recovery_numeric_profile']==COMPONENT_PROFILE and impl['hoa_recovery_slot_count']==9,'dynamic domain differs')
    require(impl['hoa_salient_component_orders']==opts['component_orders'] and impl['hoa_salient_components']==vectors.component_information(opts['component_orders']),'component metadata differs')
    require(impl['hoa_salient_order1_profile']==vectors.PROFILE and 'hoa_format_sha256' not in impl,'first-order format provenance differs')
    if counts!=[4]*5 or method:
        require(impl['hoa_salient_subband_counts']==counts and impl['hoa_salient_subband_profile']==SUBBAND_PROFILE and impl['hoa_salient_subband_format_sha256']==(partitions if method else perceptual)()['format_sha256'],'spatial metadata differs')
    if method:require(impl['hoa_salient_partition_method']==method and impl['hoa_salient_partition_profile']==PARTITION_PROFILE,'partition metadata differs')


if __name__=='__main__':raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,profile=vectors.PROFILE,
                                             frozen_name='hoa-order1-vectors-v1.json',metadata_checker=metadata))
