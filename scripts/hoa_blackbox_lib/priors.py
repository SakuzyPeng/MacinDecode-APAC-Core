"""Export only approved q6 matrix/group measurements for wider-codebook work.

Invoked in a separate process before discovery. No q7–q9 dictionary is opened and
no q6 Huffman word is exported. Full source hashes pin the approved artifacts.
"""
import json
import math
import struct

from .common import ROOT, canonical, digest, require

MATRIX_SOURCES = (
    '410345a9b07544104bfae974c778b14f8a1f61cf8d6b87f09a6e690a88ccc462',
    '9df080e3717a4c4060ed19495a5d0dfa35f06aa0018e51fe12319e34f851b5f6',
    '1b0b394772acdea3038fb436378f27cff4407b232a56f40b15b327cf58c01814',
    '58e551546c7afe705368610dd68119e5dcd11aa5e2d8c498faacb1ee7643b8c9',
)
GROUP_SOURCES = {
    '2:0': ('hoa-salient-order3-q6-mode2-book0-measured-v1.json', '78f0f21314fc565bdaf8d482fc0b5861667cf146626fab843c2e2d59e00d9c61'),
    '2:1': ('hoa-salient-order3-q6-mode2-book1-measured-v1.json', '7095b2eacf59c31cede556e6d812ba85b71315a6b0936c18724c7348a7bae804'),
    '3:0': ('hoa-salient-order3-q6-mode3-measured-v1.json', 'e67feb38d9b8b4284075fc0e55d33f71fda26ea91e54ca6633f690765b283599'),
}
COMPONENT_SHA256 = '826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd'


def read_source(name, sha):
    raw = (ROOT/'data'/name).read_bytes()
    require(digest(raw) == sha, 'qualified prior source changed: '+name)
    value = json.loads(raw)
    require(value['source']['component_sha256'] == COMPONENT_SHA256
            and value['source']['architecture'] == 'arm64', 'prior native identity differs')
    return value


def export():
    matrices, groups = {}, {}
    for cluster, sha in enumerate(MATRIX_SOURCES):
        name = f'hoa-salient-order3-mode4-cluster{cluster}-matrix-measured-v1.json'
        value = read_source(name, sha)
        words = value['matrix_f32']
        require(len(words) == 256 and all(type(v) is int and 0 <= v < 2**32
                and math.isfinite(struct.unpack('<f', struct.pack('<I', v))[0]) for v in words), 'invalid prior matrix')
        radius = value['source']['max_empirical_half_width']
        require(5e-8 <= radius < 2.5e-7, 'prior matrix precision is not qualified')
        matrices[str(cluster)] = dict(matrix_f32=words, empirical_half_width=radius,
                                      source_file=name, source_sha256=sha)
    for key, (name, sha) in GROUP_SOURCES.items():
        value = read_source(name, sha)
        group = value['coefficient_group']
        require(group and all(type(i) is int and 0 <= i < 16 for i in group)
                and len(set(group)) == len(group), 'invalid prior group')
        groups[key] = dict(indices=group, source_file=name, source_sha256=sha)
    require(sorted(groups['2:0']['indices']+groups['2:1']['indices']) == list(range(16)), 'prior partition incomplete')
    require(sorted(groups['3:0']['indices']) == list(range(16)), 'prior coefficient order incomplete')
    return dict(schema_version=1, profile='hoa-blackbox-qualified-order3-priors-v1',
                measurement_quantization_bits=6, component_sha256=COMPONENT_SHA256,
                architecture='arm64', matrices=matrices, groups=groups,
                huffman_words_included=False)


if __name__ == '__main__':
    print(canonical(export()).decode(), end='')
