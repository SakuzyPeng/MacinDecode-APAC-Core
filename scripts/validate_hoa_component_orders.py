#!/usr/bin/env python3
"""Ragged descriptor mathematics and portable fingerprints in sixteen output coefficients."""
import hoa_component_orders_vectors as vectors
from generate_hoa_salient_subbands_format import generate as perceptual,PROFILE as SUBBAND_PROFILE
from generate_hoa_salient_partition_format import generate as partitions,PROFILE as PARTITION_PROFILE
from validate_hoa_salient_subbands import main
from validate import require


def metadata(decoded,opts,report):
    impl=decoded['pcm']['decoder_settings']['implementation']['value'];counts=list(opts['counts']);method=opts.get('spatial_method',0)
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'component backend/state differs')
    require(impl['hoa_numeric_profile']==impl['hoa_descriptor_numeric_profile']==vectors.PROFILE,'component numeric profile differs')
    require(impl['hoa_salient_component_orders']==opts['component_orders'] and impl['hoa_salient_components']==vectors.component_information(opts['component_orders']),'component metadata differs')
    require('hoa_format_sha256' not in impl,'single dictionary misrepresents component orders')
    if counts!=[4]*5 or method:
        expected=(partitions if method else perceptual)()['format_sha256']
        require(impl['hoa_salient_subband_counts']==counts and impl['hoa_salient_subband_profile']==SUBBAND_PROFILE and impl['hoa_salient_subband_format_sha256']==expected,'spatial metadata differs')
    if method:require(impl['hoa_salient_partition_method']==method and impl['hoa_salient_partition_profile']==PARTITION_PROFILE,'partition metadata differs')
    else:require('hoa_salient_partition_method' not in impl,'invented partition extension')


if __name__=='__main__':
    raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,
                          profile=vectors.PROFILE,frozen_name='hoa-component-orders-vectors-v1.json',
                          metadata_checker=metadata,range_kinds=('pure','replace','add')))
