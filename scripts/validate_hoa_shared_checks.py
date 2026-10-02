#!/usr/bin/env python3
"""Shared configuration state checks and affected frozen legacy identities."""
import hashlib,json
from collections import defaultdict
from validate import require
from validate_drc import workspace
from validate_replay import command,sha256_file

def drc_regression(binary,reference,report):
    import drc_pcm_vectors as vectors
    from packet_vectors import identity
    from validate_drc_pcm import drc_structure
    from validate_packets import structure,flatten
    from validate_bwe2 import fingerprints
    from validate_cac import digest
    old=json.loads(reference.read_text());require(old['passed'] and not old['errors'],'incomplete DRC reference')
    records={(row['rate'],row['index']):row for row in old['sequences']};buckets=defaultdict(list)
    sequences=list(vectors.sequences());targets={next(i for i,(kind,opts,cases) in enumerate(sequences) if kind=='joint_tools')}
    targets.update(i for i,(kind,opts,cases) in enumerate(sequences) if kind=='metadata_updates')
    for index,(kind,options,cases) in enumerate(sequences):buckets[json.dumps(options,sort_keys=True)].append((index,kind,options,cases))
    checked=[];selected=set()
    for bucket in buckets.values():
        for first in range(0,len(bucket),8):
            batch=bucket[first:first+8]
            if not any(index in targets for index,kind,opts,cases in batch):continue
            opts=batch[0][2];rate=48000
            with workspace(report,'legacy-drc-'+str(batch[0][0])) as root:
                generated=[vectors.packet(case,rate,opts) for index,kind,options,cases in batch for case in cases]
                vectors.bundle(root/'packets',[raw for raw,truth in generated],rate,**opts)
                summary=command(binary,'parse-packets',root/'packets','--depth','packet','--packets',len(generated),'--output',root/'parsed')
                require(summary['errors']==0 and summary['packet_complete_packets']==len(generated),'legacy DRC parsing differs')
                rows=[json.loads(line)['report'] for line in (root/'parsed').read_text().splitlines()]
                decoded=command(binary,'decode-sq',root/'packets','--out',root/'pcm')
                require(decoded['pcm']['decoder_settings']['implementation']['value']==old['implementation'],'legacy DRC implementation metadata differs')
                pcm=(root/'pcm/pcm.f32le').read_bytes();cursor=0
                for index,kind,options,cases in batch:
                    count=len(cases);parts=generated[cursor:cursor+count];nodes=rows[cursor:cursor+count]
                    actual=dict(input_sha256=identity(vectors.cookie(rate,**options),[raw for raw,truth in parts]),packet_state_sha256=digest(structure(nodes)),drc_state_sha256=digest(drc_structure(nodes)),pcm_sha256=hashlib.sha256(pcm[cursor*8192:(cursor+count)*8192]).hexdigest(),**fingerprints(flatten(nodes)))
                    require(all(records[rate,index][key]==value for key,value in actual.items()),'legacy DRC state/PCM differs: '+str(index))
                    checked.append(dict(index=index,kind=kind,passed=True,**actual));cursor+=count
                    if index in targets:selected.add(index)
    require(len(selected)==3,'missing DRC tool/configuration-history representatives')
    return dict(passed=True,reference_sha256=sha256_file(reference),selected_sequences=len(selected),batch_sequences=checked)

if __name__=='__main__':
    from validate_hoa_component_orders_checks import main
    raise SystemExit(main(first_order=True,salient_counts=True,ambient_counts=True,quantization=True,expanded_orders=True,transports=True,spatial_controls=True,dynamic_domains=True,source_layouts=True,static_remapping=True,shared_configuration=True))
