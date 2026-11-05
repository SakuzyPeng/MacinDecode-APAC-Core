#!/usr/bin/env python3
"""Local, identity-bound experiments for the macOS 27.2 APAC changes.

Reports observe behavior; they do not change native acceptance expectations or
the portable decoder. All destinations must be new. PCM is stored by digest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import shutil
import shlex
import math
import itertools
import tempfile

from native_component import CURRENT_SHA256, identity, unchanged, sha256_file


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def command(binary, *args, env=None):
    p = subprocess.run([str(binary), *map(str, args)], capture_output=True,
                       text=True, env=env, timeout=180)
    try:
        result = json.loads(p.stdout or p.stderr)
    except ValueError:
        result = None
    return dict(returncode=p.returncode, result=result, stdout=p.stdout, stderr=p.stderr)


def parsed(binary, path):
    result = command(binary, 'parse-cookie', path)
    if result['returncode'] != 0 or result['result']['status'] != 'complete':
        raise ValueError('diagnostic cookie is not completely parsed: ' + str(result))
    return result['result']


def patch_bits(raw, offset, width, value):
    if not 0 <= value < (1 << width) or offset < 0 or offset + width > len(raw) * 8:
        raise ValueError('invalid bit patch')
    result = bytearray(raw)
    for i in range(width):
        at = offset + i
        mask = 1 << (7 - at % 8)
        result[at // 8] = (result[at // 8] & ~mask) | (((value >> (width - 1 - i)) & 1) * mask)
    return bytes(result)


def patch_cookie(binary, bundle, changes):
    path = bundle / 'cookie.bin'
    before = parsed(binary, path)
    raw = path.read_bytes()
    fields = {f['name']: f for f in before['fields']}
    for name, value in changes.items():
        f = fields[name]
        raw = patch_bits(raw, f['bit_offset'], f['bit_length'], value)
    path.write_bytes(raw)
    after = parsed(binary, path)
    for f in after['fields']:
        expected = dict(fields[f['name']])
        if f['name'] in changes:
            expected['value'] = changes[f['name']]
        if f != expected:
            raise ValueError('bit patch changed unrelated fields')
    manifest_path = bundle / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['file']['cookie']['value']['sha256'] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    return dict(before_sha256=before['cookie_sha256'], after_sha256=after['cookie_sha256'], changes=changes)


def retain_pcm(destination, store):
    path = destination / 'pcm.f32le'
    if not path.exists():
        return None
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    meta = json.loads((destination / 'pcm.json').read_text())
    if digest != meta['sha256'] or len(raw) != meta['frames'] * meta['channels'] * 4:
        raise ValueError('PCM identity or size differs')
    store.mkdir(exist_ok=True)
    saved = store / (digest + '.f32le')
    if saved.exists():
        if saved.read_bytes() != raw:
            raise ValueError('PCM digest collision')
        path.unlink()
    else:
        path.rename(saved)
    os.link(saved, path)
    return dict(sha256=digest, bytes=len(raw), metadata=meta,
                storage=str(saved.resolve()))


def replay(binary, bundle, destination, policy, store, env=None):
    manifest = json.loads((bundle / 'manifest.json').read_text())
    frames = manifest['file']['packet_table']['value']['valid_frames']
    result = command(binary, 'replay', bundle, '--out', destination, '--frames', frames,
                     '--processing-policy', policy, env=env)
    result['pcm'] = retain_pcm(destination, store)
    write(destination.parent / (destination.name + '.json'), result)
    if result['returncode']==0:
        if result['pcm'] is None or not result['result'].get('complete'):
            raise ValueError('successful replay did not produce complete PCM')
        meta=result['pcm']['metadata']
        fmt=manifest['file']['format']
        if (meta['frames'],meta['channels'],meta['sample_rate'],meta['all_finite'])!=(frames,fmt['channels'],fmt['sample_rate'],True):
            raise ValueError('successful replay PCM shape differs from input')
    return result


def hoa(binary, root):
    from native_expectations import cases, input_identity, COOKIE_SHA256
    from hoa_dynamic_domains_vectors import bundle
    rows = []
    for name, options, payloads in cases():
        for profile, level in [(5, 0), (5, 1), (5, 2), (0, 0)]:
            directory = root / f'{name}-p{profile}-l{level}'
            directory.mkdir()
            source = directory / 'packets'
            bundle(source, payloads, **options)
            if sha256_file(source / 'cookie.bin') != COOKIE_SHA256:
                raise ValueError('frozen cookie changed')
            patch = patch_cookie(binary, source, {'global.profile_id': profile, 'global.level_id': level})
            row = dict(name=name, profile=profile, level=level, patch=patch,
                       input=input_identity(source), runs=[])
            for repeat in range(3):
                for policy in ('default', 'drc-off'):
                    result = replay(binary, source, directory / f'{policy}-{repeat}', policy, root / 'pcm')
                    row['runs'].append(dict(repeat=repeat, policy=policy, **result))
            rows.append(row)
            print(name, profile, level, [(x['policy'], x['returncode']) for x in row['runs'][:2]], flush=True)
    for name,_,_ in cases():
        source=root/f'{name}-p5-l0'/'packets'
        frames=json.loads((source/'manifest.json').read_text())['file']['packet_table']['value']['valid_frames']
        for policy in ('default','drc-off'):
            directory=root/'traces'/f'{name}-{policy}'
            trace(binary,directory,['replay',source.resolve(),'--out',(directory/'pcm').resolve(),
                                    '--frames',frames,'--processing-policy',policy],branches=True)
    write(root / 'hoa.json', rows)
    return rows


def hoa_boundary(binary, root):
    import hoa_dynamic_domains_vectors as vectors
    rows = []
    scenarios = [(m,16,True) for m in (16,25,36,37,49)]
    scenarios += [(m,m,dynamic) for m in (36,37,49) for dynamic in (True,False)]
    for m, n, dynamic in scenarios:
        options = vectors.options(m, n, dynamic=dynamic, profile=0, level=0)
        name = f'm{m}-n{n}-dynamic{int(dynamic)}'
        directory = root / name
        directory.mkdir()
        payloads = [vectors.packet(vectors.basis(options, 0), **options)[0]]
        source = directory / 'packets'
        vectors.bundle(source, payloads, **options)
        row = dict(name=name, options=options, parsed=parsed(binary, source/'cookie.bin'), runs=[])
        for repeat in range(3):
            for policy in ('default', 'drc-off'):
                result = replay(binary, source, directory/f'{policy}-{repeat}', policy, root/'pcm')
                row['runs'].append(dict(repeat=repeat, policy=policy, **result))
        rows.append(row)
        print(name, [(x['policy'],x['returncode']) for x in row['runs'][:2]], flush=True)
        if (m,n,dynamic) in ((36,16,True),(37,16,True),(49,49,True),(49,49,False)):
            target=directory/'trace'
            trace(binary,target,['replay',source.resolve(),'--out',(target/'pcm').resolve(),
                                 '--frames',1024,'--processing-policy','drc-off'],branches=True)
            if (target/'pcm/pcm.f32le').exists():
                retain_pcm(target/'pcm',root/'pcm')
    write(root/'boundary.json', rows)


def trace(binary, root, arguments, branches=False):
    if any(k.startswith('DYLD_') and v for k,v in os.environ.items()):
        raise ValueError('config traces require an uninjected process')
    root.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, APAC_CONFIG_TRACE_OUTPUT=str((root/'trace.json').resolve()))
    if branches:
        env['APAC_CONFIG_BRANCH_TRACE'] = '1'
    script = Path(__file__).with_name('native_config_trace.py').resolve()
    before=dict(binary_sha256=sha256_file(binary),script_sha256=sha256_file(script))
    p = subprocess.run(['xcrun','lldb','--batch','--no-lldbinit',
                        '-o','command script import '+shlex.quote(str(script)),
                        '-o','run '+shlex.join(list(map(str,arguments))),
                        '-o','script native_config_trace.finish(lldb.debugger)',
                        '--',str(binary)],capture_output=True,text=True,env=env,timeout=180)
    write(root/'process.json',dict(returncode=p.returncode, arguments=list(map(str,arguments)),
                                  **before))
    (root/'lldb.log').write_text(p.stdout+p.stderr)
    result=json.loads((root/'trace.json').read_text())
    if p.returncode or result['errors'] or result['pending_returns'] or not result['probe_identity_verified']:
        raise ValueError('incomplete native config trace: '+str(result.get('errors')))
    if before!=dict(binary_sha256=sha256_file(binary),script_sha256=sha256_file(script)):
        raise ValueError('trace executable or script changed')
    return result


def origin(binary, root):
    rows=[]
    for name, args in [('stereo',[]),('hoa3-none',['--layout','hoa3','--drc-configuration','none']),
                       ('hoa3-capture',['--layout','hoa3','--drc-configuration','capture'])]:
        directory=root/name
        directory.mkdir()
        fixture=directory/'fixture'
        result=command(binary,'fixture','--out',fixture,'--signals','sine','--duration','0.125',*args)
        write(directory/'fixture.json',result)
        if result['returncode']:
            raise ValueError('fixture failed')
        source=directory/'original'
        result=command(binary,'dump',fixture/'sine/encoded.caf','--out',source)
        write(directory/'dump.json',result)
        if result['returncode']:
            raise ValueError('packet export failed')
        for value in (0,2):
            variant=directory/f'origin{value}'
            variant.mkdir()
            shutil.copytree(source,variant/'packets')
            patch=patch_cookie(binary,variant/'packets',{'extensions[0].content_origin.values[3].encoded':value+1})
            row=dict(name=name,value=value,patch=patch,packet_data_sha256=sha256_file(source/'packets.bin'),runs=[])
            if sha256_file(variant/'packets/packets.bin')!=row['packet_data_sha256']:
                raise ValueError('origin patch changed payload')
            for repeat in range(3):
                for policy in ('default','drc-off'):
                    result=replay(binary,variant/'packets',variant/f'{policy}-{repeat}',policy,root/'pcm')
                    row['runs'].append(dict(repeat=repeat,policy=policy,**result))
            rows.append(row)
            print(name,value,[(r['returncode'],r['pcm']['sha256'] if r['pcm'] else None) for r in row['runs']],flush=True)
    write(root/'origin.json',rows)
    traced=root/'initialization'
    trace(binary,traced,['fixture','--out',(traced/'fixture').resolve(),'--signals','sine','--duration','0.125'])


def dft_environment(library, directory, mode):
    if mode == 'system':
        return None
    env = dict(os.environ, DYLD_INSERT_LIBRARIES=str(library.resolve()),
               APAC_DFT_TRACE_FILE=str((directory/'dft.jsonl').resolve()))
    env.pop('APAC_DFT_OUTPUT_OFFSET',None)
    if mode != 'observe':
        env['APAC_DFT_OUTPUT_OFFSET'] = {'aligned64':'0','offset16':'16'}[mode]
    return env


def dft_audit(directory, mode):
    if mode == 'system':
        return None
    rows=[json.loads(s) for s in (directory/'dft.jsonl').read_text().splitlines()]
    audit=rows.pop()
    if not audit.get('audit') or audit['unknown'] or audit['calls'] != len(rows) or not rows:
        raise ValueError('DFT calls were not completely observed')
    if mode in ('aligned64','offset16'):
        offset=0 if mode=='aligned64' else 16
        if any(r['actual_or_mod64']!=offset or r['actual_oi_mod64']!=offset for r in rows):
            raise ValueError('DFT alignment was not controlled')
    signature=[(r['length'],r['direction'],r['input_sha256']) for r in rows]
    return dict(**audit, input_sequence_sha256=hashlib.sha256(json.dumps(signature).encode()).hexdigest(),
                output_sequence_sha256=hashlib.sha256(json.dumps([r['output_sha256'] for r in rows]).encode()).hexdigest(),
                alignments=sorted({(r['actual_or_mod64'],r['actual_oi_mod64']) for r in rows}))


def pcm_values(record):
    raw=Path(record['storage']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record['sha256']:
        raise ValueError('stored PCM identity differs')
    result=struct.unpack('<'+str(len(raw)//4)+'f',raw)
    if not all(math.isfinite(v) for v in result):
        raise ValueError('nonfinite PCM')
    return result


def compare_values(reference, candidate):
    if len(reference)!=len(candidate) or not reference:
        raise ValueError('PCM dimensions differ')
    bad=[]
    maximum=0.0
    for i,(a,b) in enumerate(zip(reference,candidate)):
        delta=abs(a-b)
        maximum=max(maximum,delta)
        if delta>1e-6+1e-5*abs(a):
            bad.append(i)
    return dict(max_absolute_error=maximum,failed_samples=len(bad),passed=not bad,
                first_failure=(dict(sample=bad[0],reference=reference[bad[0]],candidate=candidate[bad[0]]) if bad else None))


def numeric(binary, root, library):
    from validate_synthesis import sequences
    from spectrum_vectors import frame,bundle
    rows=[]
    for rate,first,targets in [(48000,4656,[4694,4699,4702,4703]),(44100,4704,[4722,4723])]:
        directory=root/str(rate)
        directory.mkdir()
        batch=list(itertools.islice(sequences(),first,first+48))
        if len(batch)!=48 or any(batch[i-first][0]!='escape_boundary' for i in targets):
            raise ValueError('SQ diagnostic case identity changed')
        source=directory/'packets'
        bundle(source,[frame(case)[0] for _,sequence in batch for case in sequence],rate)
        group=dict(rate=rate,first_index=first,targets=targets,
                   cookie_sha256=sha256_file(source/'cookie.bin'),
                   packet_data_sha256=sha256_file(source/'packets.bin'),candidates=[],runs=[])
        for repeat in range(6):
            dest=directory/f'candidate-{repeat}'
            candidate=command(binary,'decode-sq',source,'--out',dest)
            if candidate['returncode'] or candidate['result']['numeric_profile']!='apac-sq-math-v2':
                raise ValueError('candidate or SQ v2 profile differs')
            candidate['pcm']=retain_pcm(dest,root/'pcm')
            write(directory/f'candidate-{repeat}.json',candidate)
            group['candidates'].append(candidate)
            values=pcm_values(candidate['pcm'])
            for mode in ('system','observe','aligned64','offset16'):
                trial=directory/f'{mode}-{repeat}'
                trial.mkdir()
                observed=replay(binary,source,trial/'native','default',root/'pcm',
                                env=dft_environment(library,trial,mode))
                if observed['returncode'] or observed['pcm'] is None:
                    raise ValueError('native numeric replay failed')
                for key in ('mdrc','^pro','ptlc','pptl'):
                    prop=observed['pcm']['metadata']['decoder_settings'][key]
                    if prop['error'] is not None or prop['value']!=0:
                        raise ValueError('native processing settings differ')
                reference=pcm_values(observed['pcm'])
                metrics=[]
                position=0
                for i,(kind,sequence) in enumerate(batch):
                    count=len(sequence)*2048
                    metrics.append(dict(index=first+i,kind=kind,
                                        **compare_values(reference[position:position+count],values[position:position+count])))
                    position+=count
                if position!=len(reference) or len(reference)!=len(values):
                    raise ValueError('batch shape changed')
                row=dict(mode=mode,repeat=repeat,pcm=observed['pcm'],
                         dft=dft_audit(trial,mode),cases=metrics)
                group['runs'].append(row)
                write(trial/'comparison.json',row)
                print(rate,repeat,mode,'failed', [m['index'] for m in metrics if not m['passed']],flush=True)
        if len({c['pcm']['sha256'] for c in group['candidates']})!=1:
            raise ValueError('SQ v2 candidate is not repeatable')
        if len({r['dft']['input_sequence_sha256'] for r in group['runs'] if r['dft']})!=1:
            raise ValueError('DFT input sequences changed across trials')
        rows.append(group)
    write(root/'numeric.json',rows)


def origin_controlled(binary, root, library, source_root):
    rows=[]
    for name in ('stereo','hoa3-none','hoa3-capture'):
        for value in (0,2):
            source=source_root/name/f'origin{value}'/'packets'
            if not source.is_dir():
                raise ValueError('missing origin packet bundle')
            row=dict(name=name,value=value,cookie_sha256=sha256_file(source/'cookie.bin'),
                     packet_data_sha256=sha256_file(source/'packets.bin'),runs=[])
            for mode in ('aligned64','offset16'):
                for repeat in range(3):
                    for policy in ('default','drc-off'):
                        trial=root/f'{name}-v{value}-{mode}-{policy}-{repeat}'
                        trial.mkdir()
                        observed=replay(binary,source,trial/'native',policy,root/'pcm',
                                        env=dft_environment(library,trial,mode))
                        if observed['returncode']:
                            raise ValueError('origin control replay failed')
                        row['runs'].append(dict(mode=mode,repeat=repeat,policy=policy,
                                                pcm=observed['pcm'],dft=dft_audit(trial,mode)))
            rows.append(row)
            print(name,value,'controlled PCM variants',len({r['pcm']['sha256'] for r in row['runs']}),flush=True)
    write(root/'origin-controlled.json',rows)


def origin_history(binary, root, legacy_report, current_report):
    from validate import caf_payload
    old=json.loads(legacy_report.read_text())['controls']
    new=json.loads(current_report.read_text())['controls']
    if len(old)!=23 or {r['name'] for r in old}!={r['name'] for r in new}:
        raise ValueError('expected matching 23-control reports')
    lookup={r['name']:r for r in new}
    rows=[]
    for previous in old:
        name=previous['name']
        current=lookup[name]
        directory=root/name
        directory.mkdir()
        for key in ('requested','actual_encoder_settings','parameters','signal'):
            if previous[key]!=current[key]:
                raise ValueError('historical encoder settings differ: '+key)
        with tempfile.TemporaryDirectory(prefix='encoder-',dir=directory) as temp:
            fixture=Path(temp)/'fixture'
            generated=command(binary,'fixture','--out',fixture,'--signals',previous['signal'],
                              '--duration','0.125',*previous['parameters'])
            write(directory/'encoder.json',generated)
            if generated['returncode']:
                raise ValueError('history encoder control failed')
            manifest=json.loads((fixture/previous['signal']/'manifest.json').read_text())
            write(directory/'encoder-manifest.json',manifest)
            if any(manifest[k]!=previous[k] for k in ('requested','actual_encoder_settings')):
                raise ValueError('new encoder settings differ')
            cookie=caf_payload(fixture/previous['signal']/'encoded.caf',b'kuki')
        path=directory/'origin2.bin'
        path.write_bytes(cookie)
        report=parsed(binary,path)
        field=next(f for f in report['fields'] if f['name']=='extensions[0].content_origin.values[3].encoded')
        if field['value']!=3:
            raise ValueError('unexpected encoded origin minor version')
        normalized=patch_bits(cookie,field['bit_offset'],field['bit_length'],1)
        (directory/'origin0.bin').write_bytes(normalized)
        old_hash=hashlib.sha256(normalized).hexdigest()
        new_hash=hashlib.sha256(cookie).hexdigest()
        row=dict(name=name,old_cookie_sha256=old_hash,new_cookie_sha256=new_hash,
                 normalized_matches_legacy=old_hash==previous['cookie_sha256'],
                 generated_matches_current=new_hash==current['cookie_sha256'],
                 origin_bit_offset=field['bit_offset'],origin_bit_length=field['bit_length'])
        rows.append(row)
        write(directory/'comparison.json',row)
        if not row['normalized_matches_legacy'] or not row['generated_matches_current']:
            raise ValueError('cookie has additional differences: '+name)
        print(name,'only origin minor-version bits changed',flush=True)
    write(root/'origin-history.json',dict(legacy_report_sha256=sha256_file(legacy_report),
                                        current_report_sha256=sha256_file(current_report),controls=rows))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--suite', choices=['hoa','hoa-boundary','origin','numeric','origin-controlled','origin-history'], required=True)
    p.add_argument('--dft-library',type=Path)
    p.add_argument('--origin-source',type=Path)
    p.add_argument('--legacy-configs',type=Path)
    p.add_argument('--current-configs',type=Path)
    args = p.parse_args()
    if sys.platform != 'darwin':
        p.error('requires macOS')
    if any(k.startswith('DYLD_') and v for k, v in os.environ.items()):
        p.error('start without injected libraries; diagnostic injection is explicit per child')
    if args.suite in ('numeric','origin-controlled') and not args.dft_library:
        p.error('this suite requires --dft-library')
    if args.suite=='origin-controlled' and not args.origin_source:
        p.error('origin-controlled requires --origin-source')
    if args.suite=='origin-history' and not (args.legacy_configs and args.current_configs):
        p.error('origin-history requires --legacy-configs and --current-configs')
    args.out.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    component = identity()
    if component['component_sha256']!=CURRENT_SHA256:
        raise ValueError('this experiment requires the qualified 26B123 component')
    header = dict(**component, binary_sha256=sha256_file(binary),
                  script_sha256=sha256_file(__file__),
                  code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())
    if args.dft_library:
        header['dft_library_sha256']=sha256_file(args.dft_library)
    write(args.out / '.incomplete.json', header)
    if args.suite=='numeric':
        numeric(binary,args.out,args.dft_library)
    elif args.suite=='origin-controlled':
        origin_controlled(binary,args.out,args.dft_library,args.origin_source)
    elif args.suite=='origin-history':
        origin_history(binary,args.out,args.legacy_configs,args.current_configs)
    else:
        {'hoa':hoa,'hoa-boundary':hoa_boundary,'origin':origin}[args.suite](binary, args.out)
    unchanged(component)
    if sha256_file(binary) != header['binary_sha256']:
        raise ValueError('binary changed during diagnostic')
    if args.dft_library and sha256_file(args.dft_library)!=header['dft_library_sha256']:
        raise ValueError('DFT library changed during diagnostic')
    write(args.out / 'identity.json', header)
    (args.out / '.incomplete.json').unlink()


if __name__ == '__main__':
    main()
