#!/usr/bin/env python3
"""Independent carrier mapping, CPE tools, spatial recovery and PCM acceptance."""
import hoa_transport_vectors as vectors
from validate_hoa_salient_subbands import main
from validate import require


def metadata(decoded, opts, report):
    value = decoded['pcm']['decoder_settings']['implementation']['value']
    types = opts['tce_types']
    require(decoded['backend']==vectors.BACKEND and decoded['packet_state_profile']==vectors.STATE_PROFILE,'transport backend/state differs')
    require(value['hoa_transport_profile']==vectors.PROFILE,'transport profile differs')
    require(value['hoa_transport_format_sha256']==vectors.format_binding()['dependencies']['transports'],'transport format differs')
    require(value['hoa_transport_channels']==sum(vectors.width(t) for t in types),'carrier count differs')
    require(value['hoa_core_channels']==len(opts['counts'])+opts['ambient_count'],'core count differs')
    require(value['hoa_output_coefficient_count']==decoded['pcm']['channels'],'output count differs')
    require([e['tce_type'] for e in value['hoa_transport_elements']]==types,'element types differ')


if __name__=='__main__':
    raise SystemExit(main(vectors=vectors,format_generator=vectors.format_binding,format_name=None,
        frozen_name='hoa-transports-vectors-v1.json',profile=vectors.PROFILE,metadata_checker=metadata,
        range_kinds=('pair','replace','dynamic')))
