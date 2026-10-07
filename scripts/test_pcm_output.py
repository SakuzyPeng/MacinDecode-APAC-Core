"""Portable CLI container export, channel identity and failure contracts."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from portable_tools import required_binary
import channel_vectors as channels
import hoa_vectors as hoa
import hoa_shared_vectors as shared
from caf_vectors import encode as caf
from mp4_vectors import encode as mp4


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unpack_audio(raw):
    """Independent chunk reader; no production header-building helpers."""
    chunks = {}
    if raw[:4] == b'caff':
        assert raw[:8] == b'caff\0\1\0\0'
        pos = 8
        while pos < len(raw):
            tag, size = struct.unpack_from('>4sq', raw, pos)
            assert size >= 0 and pos+12+size <= len(raw)
            assert tag not in chunks
            chunks[tag] = raw[pos+12:pos+12+size]
            pos += 12+size
        assert pos == len(raw)
        assert chunks[b'desc'][8:12] == b'lpcm'
        rate, _, flags, stride, fpp, count, bits = struct.unpack('>d4sIIIII', chunks[b'desc'])
        assert (flags, stride, fpp, bits) == (3, count*4, 1, 32)
        assert chunks[b'data'][:4] == bytes(4)
        return chunks[b'data'][4:], dict(container='caf', rate=rate, channels=count, chunks=chunks)
    assert raw[:4] in (b'RIFF', b'RF64') and raw[8:12] == b'WAVE'
    rf64 = raw[:4] == b'RF64'
    assert struct.unpack_from('<I', raw, 4)[0] == (0xffffffff if rf64 else len(raw)-8)
    pos = 12
    while pos < len(raw):
        tag, size = struct.unpack_from('<4sI', raw, pos)
        if tag == b'data' and rf64:
            assert size == 0xffffffff
            size = struct.unpack_from('<Q', chunks[b'ds64'], 8)[0]
        assert pos+8+size <= len(raw) and tag not in chunks
        chunks[tag] = raw[pos+8:pos+8+size]
        pos += 8+size+(size % 2)
    assert pos == len(raw)
    fmt = chunks[b'fmt ']
    kind, count, rate, byte_rate, stride, bits, extra, valid, mask = struct.unpack('<HHIIHHHHI', fmt[:24])
    assert (len(fmt), kind, byte_rate, stride, bits, extra, valid) == (40, 0xfffe, rate*count*4, count*4, 32, 22, 32)
    assert fmt[24:] == bytes.fromhex('0300000000001000800000aa00389b71')
    frames = len(chunks[b'data']) // stride
    assert struct.unpack('<I', chunks[b'fact'])[0] == (0xffffffff if rf64 else frames)
    if rf64:
        assert struct.unpack('<QQQI', chunks[b'ds64']) == (len(raw)-8, len(chunks[b'data']), frames, 0)
    return chunks[b'data'], dict(container='rf64' if rf64 else 'wav', rate=rate, channels=count, mask=mask, chunks=chunks)


class PcmOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='apac-pcm-output-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = required_binary()
        self.counter = 0

    def path(self, suffix=''):
        self.counter += 1
        return self.root/(str(self.counter)+suffix)

    def run_tool(self, *args):
        return subprocess.run([str(self.binary), *map(str, args)], capture_output=True, text=True, encoding='utf-8')

    def channel_source(self, count=2, rate=48000, kind='caf'):
        payloads = [channels.packet(channels.excitation(count, c), count, rate)[0] for c in range(count)]
        payloads += [channels.packet({}, count, rate)[0]]*2
        path = self.path('.'+kind)
        if kind == 'bundle':
            channels.bundle(path, payloads, count, rate)
        else:
            options = dict(rate=rate, channels=count)
            if kind == 'caf':
                options['layout_tag'] = (channels.layout(count)[0] << 16) | count
            path.write_bytes((caf if kind == 'caf' else mp4)(channels.cookie(count, rate), payloads, **options)[0])
        return path

    def export(self, source, extension, *args):
        target = self.path('.'+extension)
        p = self.run_tool('decode-sq', source, '-o', target, *args)
        self.assertEqual(p.returncode, 0, p.stderr)
        report = json.loads(p.stdout)
        self.assertNotIn('pcm', report)
        self.assertTrue(report['complete'])
        self.assertFalse(report['native_apis_used'])
        raw = target.read_bytes()
        pcm, header = unpack_audio(raw)
        output = report['output']
        self.assertEqual(output['container'], header['container'])
        self.assertEqual(output['file_bytes'], len(raw))
        self.assertEqual(output['file_sha256'], digest(raw))
        self.assertEqual(output['pcm_bytes'], len(pcm))
        self.assertEqual(output['pcm_sha256'], digest(pcm))
        self.assertEqual(output['sample_rate'], header['rate'])
        self.assertEqual(output['channels'], header['channels'])
        self.assertEqual(len(pcm), output['frames']*output['channels']*4)
        self.assertFalse(list(self.root.glob('.apac-*')))
        self.assertFalse(target.with_suffix('.json').exists())
        return target, pcm, header, report

    def raw(self, source, *args):
        target = self.path()
        p = self.run_tool('decode-sq', source, '--out', target, *args)
        self.assertEqual(p.returncode, 0, p.stderr)
        report = json.loads(p.stdout)
        self.assertEqual(json.loads((target/'pcm.json').read_text()), report['pcm'])
        self.assertEqual(json.loads((target/'decode-sq.json').read_text()), report)
        self.assertNotIn('output', report)
        return (target/'pcm.f32le').read_bytes(), report

    def test_all_three_inputs_preserve_samples_and_ranges_in_every_container(self):
        for kind in ('caf', 'm4a', 'bundle'):
            for rate in (44100, 48000):
                with self.subTest(kind=kind, rate=rate):
                    source = self.channel_source(rate=rate, kind=kind)
                    full, _ = self.raw(source)
                    self.assertTrue(any(full))
                    for extension in ('wav', 'rf64', 'caf'):
                        _, actual, _, _ = self.export(source, extension)
                        self.assertEqual(actual, full)
                        for access in (['sequential', 'fast'] if kind != 'bundle' else [None]):
                            args = ['--start-frame', 1007, '--frames', 2051]
                            if access:
                                args += ['--access', access]
                            _, actual, _, report = self.export(source, extension, *args)
                            self.assertEqual(actual, full[1007*8:3058*8])
                            self.assertEqual(report['output']['start_frame'], 1007)

    def test_wave_multichannel_order_is_explicit_and_caf_keeps_source_order(self):
        orders = {1: [0], 2: [0, 1], 6: list(range(6)), 8: [0, 1, 2, 3, 6, 7, 4, 5],
                  12: [0, 1, 2, 3, 6, 7, 4, 5, 8, 9, 10, 11]}
        masks = {1: 4, 2: 3, 6: 0x3f, 8: 0x63f, 12: 0x2d63f}
        for count, order in orders.items():
            with self.subTest(channels=count):
                source = self.channel_source(count)
                reference, _ = self.raw(source)
                expected = b''.join(reference[frame+c*4:frame+c*4+4]
                                    for frame in range(0, len(reference), count*4) for c in order)
                for extension in ('wav', 'rf64'):
                    _, actual, header, report = self.export(source, extension)
                    self.assertEqual(actual, expected)
                    self.assertEqual(header['mask'], masks[count])
                    self.assertEqual(report['output']['source_channel_indices'], order)
                _, actual, _, _ = self.export(source, 'caf')
                self.assertEqual(actual, reference)

    def test_caf_retains_surround_hoa_and_composite_layouts_that_wave_rejects(self):
        sources = [self.channel_source(n) for n in (16, 24)]
        for order in range(4):
            path = self.path('.bundle')
            payloads = [hoa.packet(hoa.excitation(0, order=order), order=order)[0]]*2
            hoa.bundle(path, payloads, order=order)
            sources.append(path)
        components = [dict(type=0, options=dict(channels=2)),
                      dict(type=2, options=dict(order=1, counts=[], ambient_count=4))]
        path = self.path('.bundle')
        opts = dict(components=components, profile=0)
        shared.bundle(path, [shared.packet({}, **opts)[0]]*2, **opts)
        sources.append(path)
        for source in sources:
            with self.subTest(source=source.name):
                reference, raw_report = self.raw(source)
                _, actual, header, report = self.export(source, 'caf')
                self.assertEqual(actual, reference)
                layout = report['output']['source_layout']
                self.assertEqual({k: v for k, v in layout.items() if k != 'name'},
                                 {k: v for k, v in raw_report['pcm']['layout']['value'].items() if k != 'name'})
                tag, bitmap, count = struct.unpack_from('>III', header['chunks'][b'chan'])
                self.assertEqual((tag, bitmap, count), (layout['tag'], layout['bitmap'], len(layout['descriptions'])))
                for i, description in enumerate(layout['descriptions']):
                    label, flags, *coordinates = struct.unpack_from('>IIfff', header['chunks'][b'chan'], 12+20*i)
                    self.assertEqual((label, flags, coordinates), (description['label'], description['flags'], description['coordinates']))
                for extension in ('wav', 'rf64'):
                    target = self.path('.'+extension)
                    p = self.run_tool('decode-sq', source, '-o', target)
                    self.assertEqual(p.returncode, 1)
                    self.assertIn('-o output.caf', p.stderr)
                    self.assertFalse(target.exists())
                    self.assertFalse(list(self.root.glob('.apac-*')))

    def test_cli_destinations_format_inference_and_empty_output(self):
        source = self.channel_source()
        for args in [[], ['--out', self.path(), '-o', self.path('.wav')],
                     ['--out', self.path(), '--format', 'caf'],
                     ['-o', self.path('.wav'), '--format', 'flac']]:
            p = self.run_tool('decode-sq', source, *args)
            self.assertEqual(p.returncode, 2, (args, p.stderr))
        target = self.path('.unknown')
        p = self.run_tool('decode-sq', source, '-o', target)
        self.assertEqual(p.returncode, 1)
        self.assertIn('--format', p.stderr)
        self.assertFalse(target.exists())
        self.export(source, 'WAVE')
        _, _, header, _ = self.export(source, 'wav', '--format', 'rf64')
        self.assertEqual(header['container'], 'rf64')
        self.export(source, 'audio', '--format', 'caf')
        for extension in ('wav', 'rf64', 'caf'):
            _, actual, _, report = self.export(source, extension, '--start-frame', 4096, '--frames', 1)
            self.assertEqual(actual, b'')
            self.assertEqual(report['output']['frames'], 0)

    def test_existing_paths_symlinks_failures_and_quotas_leave_no_partial_file(self):
        source = self.channel_source()
        for extension in ('wav', 'rf64', 'caf'):
            target = self.path('.'+extension)
            target.write_bytes(b'preserve this')
            p = self.run_tool('decode-sq', source, '-o', target)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(target.read_bytes(), b'preserve this')
            folder = self.path('.'+extension)
            folder.mkdir()
            self.assertEqual(self.run_tool('decode-sq', source, '-o', folder).returncode, 1)
            limited = self.path('.'+extension)
            self.assertEqual(self.run_tool('decode-sq', source, '-o', limited, '--max-output-mib', 0).returncode, 1)
            self.assertFalse(limited.exists())
            bad = self.path('.caf')
            bad.write_bytes(caf(channels.cookie(2), [channels.packet({}, 2)[0], b'\xff'])[0])
            target = self.path('.'+extension)
            p = self.run_tool('decode-sq', bad, '-o', target)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertFalse(target.exists())
            self.assertFalse(list(self.root.glob('.apac-*')))
        link = self.path('.wav')
        try:
            link.symlink_to(self.root/'missing-target')
        except OSError as error:
            if getattr(error, 'winerror', None) != 1314:
                raise
        else:
            self.assertEqual(self.run_tool('decode-sq', source, '-o', link).returncode, 1)
            self.assertTrue(link.is_symlink())
            self.assertFalse((self.root/'missing-target').exists())

    def test_file_mode_has_no_default_quota_without_writing_a_large_file(self):
        # Declares >128 MiB of decoded audio, but fails on the very first packet.
        # The distinct failures establish whether a quota stops us before decoding.
        source = self.path('.caf')
        source.write_bytes(caf(channels.cookie(24), [b'\xff']*2000, channels=24)[0])
        direct = self.path('.caf')
        p = self.run_tool('decode-sq', source, '-o', direct)
        self.assertEqual(p.returncode, 1)
        self.assertNotEqual(json.loads(p.stderr)['error']['operation'], 'output limit')
        self.assertEqual(json.loads(p.stderr)['error']['packet_index'], 0)
        self.assertFalse(direct.exists())
        for args in [('-o', self.path('.caf'), '--max-output-mib', 128), ('--out', self.path())]:
            p = self.run_tool('decode-sq', source, *args)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(json.loads(p.stderr)['error']['operation'], 'output limit')
        self.assertFalse(list(self.root.glob('.apac-*')))

    def caf_chan(self, source):
        raw = source.read_bytes()
        at = 8
        while at < len(raw):
            tag, size = struct.unpack_from('>4sq', raw, at)
            if tag == b'chan':
                return raw, at, size
            at += 12+size
        self.fail('fixture has no chan chunk')

    def test_manual_input_layout_repairs_caf_tags_without_changing_source_or_pcm(self):
        source = self.channel_source(8)
        reference, _ = self.raw(source)
        _, wave_reference, _, _ = self.export(source, 'wav')
        raw, at, size = self.caf_chan(source)
        changed = bytearray(raw)
        struct.pack_into('>I', changed, at+12, (147 << 16) | 8)
        source.write_bytes(changed)
        original_sha = digest(source.read_bytes())
        target = self.path('.caf')
        strict = self.run_tool('decode-sq', source, '-o', target)
        self.assertEqual(strict.returncode, 1)
        self.assertFalse(target.exists())
        _, actual, _, report = self.export(source, 'wav', '--input-layout', '7.1')
        self.assertEqual(actual, wave_reference)
        audit = report['input']['layout_override']
        self.assertEqual(audit['profile'], 'apac-caf-layout-override-v1')
        self.assertEqual(audit['original']['tag'], (147 << 16) | 8)
        self.assertEqual(audit['original']['sha256'], digest(changed[at+12:at+12+size]))
        self.assertFalse(audit['original_matched_cookie'])
        self.assertEqual(audit['effective']['tag'], (128 << 16) | 8)
        self.assertTrue(report['input']['consistency_verified'])
        actual, report = self.raw(source, '--input-layout', 'surround71')
        self.assertEqual(actual, reference)
        self.assertIn('layout_override', report['input'])
        _, actual, _, report = self.export(source, 'caf', '--input-layout', '7.1', '--access', 'fast',
                                         '--start-frame', 1007, '--frames', 2051)
        self.assertEqual(actual, reference[1007*32:3058*32])
        self.assertTrue(report['input']['consistency_verified'])
        self.assertEqual(digest(source.read_bytes()), original_sha)

    def test_missing_caf_layout_already_uses_cookie_and_explicit_choice_is_audited(self):
        source = self.channel_source(2)
        raw, at, size = self.caf_chan(source)
        source.write_bytes(raw[:at]+raw[at+12+size:])
        _, automatic, _, report = self.export(source, 'wav')
        self.assertEqual(report['input']['layout_source'], 'cookie')
        self.assertNotIn('layout_override', report['input'])
        _, explicit, _, report = self.export(source, 'wav', '--input-layout', 'stereo')
        self.assertEqual(explicit, automatic)
        audit = report['input']['layout_override']
        self.assertIsNone(audit['original'])
        self.assertIsNone(audit['original_matched_cookie'])

    def test_manual_input_layout_cannot_relabel_channels_or_bypass_bad_metadata(self):
        source = self.channel_source(16)
        for layout in ('hoa3', 'stereo'):
            target = self.path('.caf')
            p = self.run_tool('decode-sq', source, '-o', target, '--input-layout', layout)
            self.assertEqual(p.returncode, 1)
            self.assertIn('disagrees with APAC cookie', p.stderr)
            self.assertFalse(target.exists())
        _, _, _, report = self.export(source, 'caf', '--input-layout', '9.1.6')
        self.assertTrue(report['input']['layout_override']['original_matched_cookie'])
        raw, at, _ = self.caf_chan(source)
        for position, value in ((at+20, 1), (44, 2)):
            changed = bytearray(raw)
            struct.pack_into('>I', changed, position, value)
            source.write_bytes(changed)
            target = self.path('.caf')
            self.assertEqual(self.run_tool('decode-sq', source, '-o', target, '--input-layout', '9.1.6').returncode, 1)
            self.assertFalse(target.exists())
        for kind in ('m4a', 'bundle'):
            path = self.channel_source(kind=kind)
            target = self.path('.wav')
            p = self.run_tool('decode-sq', path, '-o', target, '--input-layout', 'stereo')
            self.assertEqual(p.returncode, 1)
            self.assertIn('CAF container tags only', p.stderr)
            self.assertFalse(target.exists())
        p = self.run_tool('decode-sq', source, '-o', self.path('.caf'), '--input-layout', 'guess')
        self.assertEqual(p.returncode, 2)
        self.assertFalse(list(self.root.glob('.apac-*')))

    def test_manual_hoa_layout_keeps_its_domain_and_normalization(self):
        source = self.path('.caf')
        packets = [hoa.packet(hoa.excitation(0))[0]]*2
        source.write_bytes(caf(hoa.cookie(), packets, channels=16, layout_tag=(193 << 16) | 16)[0])
        target = self.path('.caf')
        self.assertEqual(self.run_tool('decode-sq', source, '-o', target).returncode, 1)
        self.assertFalse(target.exists())
        _, _, header, report = self.export(source, 'caf', '--input-layout', 'hoa3')
        self.assertEqual(struct.unpack_from('>I', header['chunks'][b'chan'])[0], (190 << 16) | 16)
        self.assertEqual(report['output']['source_layout']['ambisonic_normalization'], 'SN3D')
        for layout in ('9.1.6', 'hoa3-n3d'):
            target = self.path('.caf')
            p = self.run_tool('decode-sq', source, '-o', target, '--input-layout', layout)
            self.assertEqual(p.returncode, 1)
            self.assertIn('disagrees with APAC cookie', p.stderr)
            self.assertFalse(target.exists())

    @unittest.skipUnless(shutil.which('ffprobe') and shutil.which('ffmpeg'), 'FFmpeg tools are optional independent readers')
    def test_ffmpeg_reads_all_containers_without_changing_samples(self):
        source = self.channel_source(8)
        for extension in ('wav', 'rf64', 'caf'):
            target, expected, _, _ = self.export(source, extension)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-select_streams', 'a:0',
                                    '-of', 'json', str(target)], check=True, capture_output=True, text=True)
            stream = json.loads(probe.stdout)['streams'][0]
            self.assertEqual((stream['codec_name'], int(stream['sample_rate']), stream['channels']), ('pcm_f32le', 48000, 8))
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(target), '-map', '0:a:0',
                                      '-c:a', 'pcm_f32le', '-f', 'f32le', '-'], check=True, capture_output=True)
            self.assertEqual(decoded.stdout, expected)

    @unittest.skipUnless(shutil.which('afinfo'), 'afinfo is only available on macOS')
    def test_core_audio_accepts_pcm_caf_with_discrete_and_hoa_layouts(self):
        sources = [(self.channel_source(16), '9.1.6'), (self.channel_source(24), '22.2')]
        path = self.path('.bundle')
        hoa.bundle(path, [hoa.packet({})[0]])
        sources.append((path, 'ACN/SN3D'))
        for source, layout in sources:
            target, _, _, _ = self.export(source, 'caf')
            result = subprocess.run(['afinfo', str(target)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Float32', result.stdout)
            self.assertIn(layout, result.stdout)


if __name__ == '__main__':
    unittest.main()
