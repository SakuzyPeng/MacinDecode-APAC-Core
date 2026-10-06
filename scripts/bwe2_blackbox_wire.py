"""Controlled BWE2 streams. Only public-source AAC tables are read here."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def bits(value, width):
    if type(value) is not int or not 0 <= value < (1 << width):
        raise ValueError('integer outside wire field')
    return format(value, f'0{width}b') if width else ''


def pack(wire):
    wire += '0' * (-len(wire) % 8)
    return int(wire, 2).to_bytes(len(wire)//8, 'big')


def cookie():
    fields = [(0,32),(int.from_bytes(b'dapa','big'),32),(0,32),(0x800,16),
              (31,6),(0,4),(0,1),(3,6),(0,6),(2,8),(2,8),(0,1),(1,3),
              (0,8),(0,3),(0,1),(1,5),(1,3),(101,16),(0,1),(0,1),
              (0,3),(0,2),(0,5),(0,1)]
    raw = pack(''.join(bits(v,w) for v,w in fields))
    return len(raw).to_bytes(4,'big') + raw[4:]


class Writer:
    def __init__(self):
        self.tables = json.loads((ROOT/'data/sq-codebooks.json').read_text())
        self.offsets = self.tables['long_offsets']

    def channel(self, values, gain):
        if len(values) != 1024 or any(v not in (-1,0,1) for v in values):
            raise ValueError('writer requires 1024 ternary coefficients')
        present = [any(values[a:b]) for a,b in zip(self.offsets,self.offsets[1:])]
        maximum = max((i+1 for i,v in enumerate(present) if v), default=0)
        header = '00' + bits(maximum,6) + bits(gain,8)
        spectral = ''
        sf = self.tables['scalefactor']
        book = self.tables['spectral'][0]
        band = 0
        while band < maximum:
            active = present[band]
            end = band+1
            while end < maximum and present[end] == active:
                end += 1
            length = end-band
            header += bits(int(active),4) + bits(31,5)*(length//31) + bits(length%31,5)
            if active:
                for b in range(band,end):
                    header += bits(sf['codes'][60],sf['bits'][60])
                    for k in range(self.offsets[b],self.offsets[b+1],4):
                        index = 0
                        for value in values[k:k+4]:
                            index = index*3+value+1
                        spectral += bits(book['codes'][index],book['bits'][index])
            band = end
        return header+spectral

    def packet(self, left, gain=128, parameters=None, right=None, right_parameters=None):
        right = [0]*1024 if right is None else right
        wire = '0110'+self.channel(left,gain)+'0'+self.channel(right,gain)+'00'
        flags = (parameters is not None, right_parameters is not None)
        wire += ''.join(bits(int(v),1) for v in flags)
        for values, params in ((left,parameters),(right,right_parameters)):
            if params is not None:
                if not any(values):
                    raise ValueError('BWE2 control requires a nonempty coded spectrum')
                i,j,g = params
                wire += bits(i,9)+bits(j,9)+bits(g,6)
        return pack(wire)+b'\0'

    def program(self, spec):
        values = source(spec.get('source','comb'), spec.get('seed',1), spec.get('line',0), spec.get('cutoff',256))
        params = tuple(spec['parameters']) if spec.get('parameters') is not None else None
        target = self.packet(values,spec.get('gain',128),params)
        silence = self.packet([0]*1024)
        return [target,silence]


def source(kind='comb', seed=1, line=0, cutoff=256):
    values = [0]*1024
    if kind == 'silence':
        return values
    if kind == 'tone':
        if not 0 <= line < 1024:
            raise ValueError('tone line out of range')
        values[line] = 1
        return values
    if kind not in ('comb','flat') or cutoff not in (256,384):
        raise ValueError('unsupported carrier')
    # Odd-bin power is an exact periodic comb in the symmetric 2*cutoff DFT.
    # Its first 16 nonzero-lag correlations vanish in the mathematical model.
    state = seed & 0xffffffff
    for k in range(1 if kind == 'comb' else 0,cutoff,2 if kind == 'comb' else 1):
        state = (1664525*state+1013904223)&0xffffffff
        values[k] = 1 if state&0x80000000 else -1
    return values


def bundle(folder, packets):
    folder.mkdir()
    raw,cfg = b''.join(packets),cookie()
    known = lambda v:dict(value=v,error=None)
    layout = dict(tag=(101<<16)|2,bitmap=0,descriptions=[],ambisonic_order=None,
                  ambisonic_channel_order=None,ambisonic_normalization=None)
    info = dict(schema_version=1,source='bwe2-blackbox-synthetic.caf',file_bytes=len(raw),modified_unix_seconds=None,
        environment=dict(tool_version='bwe2-blackbox-v1',os='synthetic',architecture='portable',system_version='synthetic'),
        container=known('caff'),format=dict(sample_rate=48000,format_id=int.from_bytes(b'apac','big'),format_fourcc='apac',
        flags=0,bytes_per_packet=0,frames_per_packet=1024,bytes_per_frame=0,channels=2,bits_per_channel=0),
        layout=known(layout),packet_count=known(len(packets)),max_packet_bytes=known(max(map(len,packets))),
        packet_table=known(dict(priming_frames=0,valid_frames=1024*len(packets),remainder_frames=0)),
        cookie=known(dict(bytes=len(cfg),sha256=digest(cfg))),restricts_random_access=known(False))
    manifest = dict(schema_version=1,complete=True,file=info,start_packet=0,requested_packets=len(packets),
        actual_packets=len(packets),packet_data_file='packets.bin',packet_index_file='packets.jsonl',cookie_file='cookie.bin',
        packet_data_bytes=len(raw),packet_data_sha256=digest(raw))
    offset,rows = 0,[]
    for i,p in enumerate(packets):
        rows.append(dict(schema_version=1,packet_index=i,export_offset=offset,bytes=len(p),frames=1024,sha256=digest(p),
            raw_frame_position=known(i*1024),dependency=known(dict(independently_decodable=True,preroll_packet_count=0)),roll_distance=known(0)))
        offset += len(p)
    (folder/'manifest.json').write_bytes(canonical(manifest))
    (folder/'cookie.bin').write_bytes(cfg)
    (folder/'packets.bin').write_bytes(raw)
    (folder/'packets.jsonl').write_bytes(b''.join(canonical(r)+b'\n' for r in rows))
