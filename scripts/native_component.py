"""Versioned identities for public observations and read-only native probes."""
import hashlib
from pathlib import Path
import struct
import uuid


COMPONENT = Path('/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs')
LEGACY_SHA256 = '826948774145d657788f3101cf36ad1103c230e9bb3712cb65bc56763fd297dd'
CURRENT_SHA256 = '5bd433b1cd97b1a93d1b5c389c1b46314757045a51c9e51d3cbf5212cab294b2'
CURRENT_RUNTIME_UUID = '61C44B37-3B5D-3376-90AC-8E1706FF3B20'
COMPONENT_ENTRY_PATTERN = r'^ACAPACDecoderFactory$'
PROBE_FAMILIES = (
    'frame', 'channels', 'packet', 'drc', 'ancillary', 'hoa', 'hoa_orders',
    'hoa_static', 'hoa_salient', 'hoa_mixed', 'hoa_dynamic', 'hoa_additive',
    'hoa_transports', 'hoa_salient_subbands', 'hoa_dynamic_subbands',
    'hoa_remapping', 'hoa_source_layouts', 'cac_tables', 'bwe2_tables',
    'drc_tables', 'hoa_tables',
)
PROFILES = {
    LEGACY_SHA256: dict(build='26A428', system='macOS 27.0', layout='apac-native-26A428-v1',
                        probes=PROBE_FAMILIES),
    CURRENT_SHA256: dict(build='26B123', system='macOS 27.2', layout='apac-native-26B123-v1',
                         probes=PROBE_FAMILIES),
}
TABLE_ADDRESSES = {
    LEGACY_SHA256: dict(cac=(0x8a1200, 0x8a1444, 0x8a128c, 0x8a133c),
                       bwe2=(0x8a29a4, 0x8aa9a4, 0xaad6e0, 0xa2f430),
                       drc=(0x6527b4, 0x6529a0, 0x6529a8, 0x6529dc, 0x6529e0, 0x652a00),
                       hoa=0xaaa990),
    CURRENT_SHA256: dict(cac=(0x8a0e80, 0x8a10c4, 0x8a0f0c, 0x8a0fbc),
                        bwe2=(0x8a2624, 0x8aa624, 0xaad700, 0xa2f0b0),
                        drc=(0x652834, 0x652a20, 0x652a28, 0x652a5c, 0x652a60, 0x652a80),
                        hoa=0xaaa9b0),
}


def sha256_file(path=COMPONENT):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def profile(digest, family=None):
    try:
        result = PROFILES[digest]
    except KeyError as error:
        raise ValueError('unqualified AudioCodecs component: ' + str(digest)) from error
    if family is not None and family not in result['probes']:
        raise ValueError('unqualified native probe family: ' + family)
    return result


def slices(raw):
    """Read the bounded Mach-O slice identities without loading executable code."""
    if len(raw) < 8 or struct.unpack_from('>I', raw)[0] != 0xcafebabe:
        raise ValueError('expected a universal Mach-O component')
    count = struct.unpack_from('>I', raw, 4)[0]
    if not 0 < count <= 16 or 8 + count * 20 > len(raw):
        raise ValueError('invalid Mach-O slice table')
    result = []
    for index in range(count):
        cpu, subtype, offset, size, _ = struct.unpack_from('>IIIII', raw, 8 + 20 * index)
        if size < 32 or offset + size > len(raw):
            raise ValueError('Mach-O slice outside component')
        data = memoryview(raw)[offset:offset + size]
        if struct.unpack_from('<I', data)[0] != 0xfeedfacf:
            raise ValueError('expected a 64-bit Mach-O slice')
        commands, command_bytes = struct.unpack_from('<II', data, 16)
        end = 32 + command_bytes
        if end > size or commands > command_bytes // 8:
            raise ValueError('invalid Mach-O load commands')
        position = 32
        identifier = None
        for _ in range(commands):
            if position + 8 > end:
                raise ValueError('truncated Mach-O load command')
            kind, length = struct.unpack_from('<II', data, position)
            if length < 8 or position + length > end:
                raise ValueError('invalid Mach-O load command length')
            if kind == 0x1b:
                if length != 24 or identifier is not None:
                    raise ValueError('invalid Mach-O UUID command')
                identifier = str(uuid.UUID(bytes=bytes(data[position + 8:position + 24]))).upper()
            position += length
        if position != end or identifier is None:
            raise ValueError('incomplete Mach-O identity')
        result.append(dict(cpu_type=cpu, cpu_subtype=subtype, uuid=identifier,
                           offset=offset, bytes=size))
    return result


def identity(path=COMPONENT):
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    known = PROFILES.get(digest)
    return dict(component_sha256=digest,
                component_profile=known['layout'] if known else None,
                component_build=known['build'] if known else None,
                component_slices=slices(raw))


def unchanged(expected, path=COMPONENT):
    if sha256_file(path) != expected['component_sha256']:
        raise ValueError('AudioCodecs identity changed during validation')


def validate_capture(capture, family=None):
    selected = profile(capture['component_sha256'], family)
    if capture.get('component_profile', selected['layout']) != selected['layout']:
        raise ValueError('native capture layout profile differs from component')
    if capture['component_sha256'] != LEGACY_SHA256:
        if capture.get('component_profile') != selected['layout']:
            raise ValueError('native capture has no verified layout profile')
        if not capture.get('probe_identity_verified') or not capture.get('loaded_component'):
            raise ValueError('native capture has no verified loaded component')
        if not capture.get('breakpoint_hits') or not any(capture['breakpoint_hits'].values()):
            raise ValueError('native capture has no breakpoint observations')
        families = capture.get('probe_families', [])
        if not families or any(item not in selected['probes'] for item in families):
            raise ValueError('native capture has an unqualified probe family')
        if family is not None and family not in families:
            raise ValueError('required probe family absent from native capture')
        loaded = capture['loaded_component']
        if loaded.get('uuid') != CURRENT_RUNTIME_UUID:
            raise ValueError('native capture has an unqualified runtime slice')
        if loaded.get('cpu_type') != 0x100000c or loaded.get('cpu_subtype') != 0x80000002:
            raise ValueError('native capture architecture differs')
    return True


def bind_capture(report, capture, family=None):
    validate_capture(capture, family)
    digest = capture['component_sha256']
    previous = report.get('component_sha256')
    if previous is not None and previous != digest:
        raise ValueError('mixed AudioCodecs versions in one native validation')
    report['component_sha256'] = digest
    report['component_profile'] = profile(digest)['layout']
    return True


class ProbeSession:
    """Bind memory observations to the exact on-disk and loaded component."""

    def __init__(self, target, family):
        if not target.GetTriple().startswith('arm64'):
            raise ValueError('native probes require arm64 macOS')
        self.identity = identity()
        profile(self.identity['component_sha256'], family)
        self.target = target
        self.families = {family}
        self.breakpoints = {}
        self.hits = {}
        self.loaded = None
        self.errors = []

    def add_family(self, family):
        profile(self.identity['component_sha256'], family)
        self.families.add(family)

    def breakpoint(self, pattern):
        breakpoint = self.target.BreakpointCreateByRegex(pattern, 'AudioCodecs')
        self.breakpoints[breakpoint.GetID()] = pattern
        return breakpoint

    def observe(self, frame, location):
        if self.loaded is None:
            modules = [module for module in self.target.module_iter()
                       if module.GetFileSpec().GetFilename() == 'AudioCodecs']
            if len(modules) != 1:
                raise ValueError('expected one loaded AudioCodecs module')
            module = modules[0]
            identifier = module.GetUUIDString().upper()
            matches = [item for item in self.identity['component_slices']
                       if item['uuid'] == identifier and item['cpu_type'] == 0x100000c]
            if len(matches) != 1:
                raise ValueError('loaded AudioCodecs slice differs from verified file')
            if (self.identity['component_sha256'] == CURRENT_SHA256
                    and identifier != CURRENT_RUNTIME_UUID):
                raise ValueError('AudioCodecs runtime slice has no qualified memory layout')
            unchanged(self.identity)
            self.loaded = dict(uuid=identifier, triple=module.GetTriple(),
                               cpu_type=matches[0]['cpu_type'], cpu_subtype=matches[0]['cpu_subtype'])
        key = location.GetBreakpoint().GetID()
        if key in self.breakpoints:
            pattern = self.breakpoints[key]
            self.hits[pattern] = location.GetBreakpoint().GetHitCount()

    def finish(self, report):
        report.setdefault('errors', []).extend(self.errors)
        report.update(self.identity)
        report.update(probe_families=sorted(self.families), loaded_component=self.loaded,
                      breakpoint_hits=self.hits, probe_identity_verified=False)
        try:
            unchanged(self.identity)
            if self.errors:
                raise ValueError('native breakpoint identity verification failed')
            if self.loaded is None or not any(self.hits.values()):
                raise ValueError('native probe did not observe any breakpoint')
            if report.get('process_exit_code') == 0 and not any(
                    count for pattern, count in self.hits.items() if pattern != COMPONENT_ENTRY_PATTERN):
                raise ValueError('successful replay did not reach the requested native probes')
            for key, pattern in self.breakpoints.items():
                breakpoint = self.target.FindBreakpointByID(key)
                if breakpoint.IsValid() and breakpoint.GetNumLocations() == 0:
                    raise ValueError('native symbol breakpoint has no locations: ' + pattern)
            report['probe_identity_verified'] = True
        except Exception as error:
            report.setdefault('errors', []).append(str(error))
        return report
