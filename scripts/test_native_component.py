"""Component identity failures must precede native memory observations."""
import copy
import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import native_component as component


def current_capture():
    return dict(component_sha256=component.CURRENT_SHA256,
                component_profile=component.PROFILES[component.CURRENT_SHA256]['layout'],
                probe_identity_verified=True, probe_families=['frame'],
                loaded_component=dict(uuid=component.CURRENT_RUNTIME_UUID,
                                      cpu_type=0x100000c, cpu_subtype=0x80000002),
                breakpoint_hits={'packet': 1})


class ComponentTests(unittest.TestCase):
    def test_known_versions_and_unknown_components(self):
        self.assertEqual(component.profile(component.LEGACY_SHA256)['build'], '26A428')
        self.assertEqual(component.profile(component.CURRENT_SHA256)['build'], '26B123')
        with self.assertRaises(ValueError):
            component.profile('unknown')
        with self.assertRaises(ValueError):
            component.profile(component.CURRENT_SHA256, 'unknown_probe')

    def test_legacy_capture_needs_no_new_fields(self):
        capture = dict(component_sha256=component.LEGACY_SHA256)
        report = dict(component_sha256=None)
        self.assertTrue(component.bind_capture(report, capture, 'hoa'))
        self.assertEqual(report['component_profile'], 'apac-native-26A428-v1')

    def test_current_capture_requires_layout_slice_and_observations(self):
        original = current_capture()
        self.assertTrue(component.validate_capture(original, 'frame'))
        for key, value in [('component_profile', 'incorrect'),
                           ('probe_identity_verified', False),
                           ('loaded_component', {}), ('breakpoint_hits', {}),
                           ('probe_families', ['unknown'])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                component.validate_capture(dict(original, **{key: value}), 'frame')
        altered = copy.deepcopy(original)
        altered['loaded_component']['uuid'] = 'incorrect'
        with self.assertRaises(ValueError):
            component.validate_capture(altered)
        with self.assertRaises(ValueError):
            component.validate_capture(original, 'hoa')

    def test_capture_batches_cannot_mix_versions(self):
        report = {}
        component.bind_capture(report, dict(component_sha256=component.LEGACY_SHA256))
        with self.assertRaises(ValueError):
            component.bind_capture(report, current_capture())

    def test_public_identity_accepts_an_unknown_version_and_detects_changes(self):
        header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 2, 6, 1, 24, 0, 0)
        image = header + struct.pack('<II', 0x1b, 24) + bytes(range(16))
        raw = struct.pack('>7I', 0xcafebabe, 1, 0x100000c, 2, 28, len(image), 0) + image
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'component'
            path.write_bytes(raw)
            identity = component.identity(path)
            self.assertIsNone(identity['component_profile'])
            component.unchanged(identity, path)
            path.write_bytes(raw + b'x')
            with self.assertRaises(ValueError):
                component.unchanged(identity, path)
        with self.assertRaises(ValueError):
            component.slices(raw[:-1])

    def session(self):
        target = Mock()
        target.GetTriple.return_value = 'arm64-apple-macosx27.2'
        identity = dict(component_sha256=component.CURRENT_SHA256,
                        component_slices=[dict(uuid=component.CURRENT_RUNTIME_UUID,
                                               cpu_type=0x100000c, cpu_subtype=0x80000002)])
        with patch.object(component, 'identity', return_value=identity):
            return component.ProbeSession(target, 'frame')

    def test_missing_breakpoint_and_empty_capture_fail(self):
        session = self.session()
        with patch.object(component, 'unchanged'):
            result = session.finish(dict(errors=[]))
            self.assertFalse(result['probe_identity_verified'])
            self.assertIn('did not observe', result['errors'][0])
            session.loaded = current_capture()['loaded_component']
            session.hits = {'packet': 1}
            session.breakpoints = {7: 'missing_symbol'}
            session.target.FindBreakpointByID.return_value.GetNumLocations.return_value = 0
            result = session.finish(dict(errors=[]))
            self.assertFalse(result['probe_identity_verified'])
            self.assertIn('no locations', result['errors'][0])

    def test_loaded_uuid_is_checked_before_reads(self):
        session = self.session()
        module = Mock()
        module.GetFileSpec.return_value.GetFilename.return_value = 'AudioCodecs'
        module.GetUUIDString.return_value = 'wrong'
        session.target.module_iter.return_value = [module]
        with self.assertRaises(ValueError):
            session.observe(Mock(), Mock())
        self.assertIsNone(session.loaded)

    def test_component_or_tool_change_during_capture_fails(self):
        session = self.session()
        session.loaded = current_capture()['loaded_component']
        session.hits = {'packet': 1}
        with patch.object(component, 'unchanged', side_effect=ValueError('identity changed')):
            result = session.finish(dict(errors=[]))
        self.assertFalse(result['probe_identity_verified'])
        self.assertEqual(result['errors'], ['identity changed'])
        import native_expectations as expectations
        from hoa_dynamic_domains_vectors import bundle
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'mapac'
            binary.write_bytes(b'original tool')
            _, options, packets = next(expectations.cases())
            bundle(root/'packets', packets, **options)

            def changed_tool(arguments, **kwargs):
                binary.write_bytes(b'changed tool')
                error = dict(os_status=560226676, os_status_fourcc='!dat',
                             operation=expectations.COOKIE_OPERATIONS[arguments[-1]])
                return CompletedProcess(arguments, 1, '', json.dumps(dict(error=error)))

            with patch.object(expectations, 'identity', return_value={'component_sha256': component.CURRENT_SHA256}), \
                 patch.object(expectations, 'unchanged'), \
                 patch.object(expectations.subprocess, 'run', side_effect=changed_tool), \
                 self.assertRaisesRegex(ValueError, 'executable changed'):
                expectations.collect(binary, root/'packets')


class NativeAcceptanceTests(unittest.TestCase):
    def test_generated_inputs_match_the_two_frozen_identities(self):
        import hashlib
        from hoa_dynamic_domains_vectors import cookie
        from native_expectations import COOKIE_SHA256, PACKET_SHA256, cases
        for name, options, packets in cases():
            self.assertEqual(hashlib.sha256(cookie(**options)).hexdigest(), COOKIE_SHA256)
            self.assertEqual(hashlib.sha256(b''.join(packets)).hexdigest(), PACKET_SHA256[name])

    def test_rejection_is_bound_to_version_input_policy_and_stage(self):
        from native_expectations import COOKIE_SHA256, PACKET_SHA256, COOKIE_OPERATIONS, validate_result
        inputs = dict(cookie_sha256=COOKIE_SHA256, packet_data_sha256=PACKET_SHA256['dimension-49-to-16'],
                      channels=16, sample_rate=48000, frames=1024, layout_tag=12451856)
        for policy, operation in COOKIE_OPERATIONS.items():
            result = dict(error=dict(os_status=560226676, os_status_fourcc='!dat', operation=operation))
            validate_result(component.CURRENT_SHA256, inputs, 1, result, policy)
            validate_result(component.LEGACY_SHA256, inputs, 0, dict(complete=True), policy)
            for digest, data, code, observed in [
                (component.LEGACY_SHA256, inputs, 1, result),
                (component.CURRENT_SHA256, dict(inputs, channels=9), 1, result),
                (component.CURRENT_SHA256, dict(inputs, packet_data_sha256='wrong'), 1, result),
                (component.CURRENT_SHA256, inputs, 0, dict(complete=True)),
                (component.CURRENT_SHA256, inputs, 1, dict(error=dict(result['error'], operation='decode'))),
            ]:
                with self.assertRaises(ValueError):
                    validate_result(digest, data, code, observed, policy)


class NativeCliIdentityTests(unittest.TestCase):
    def test_packet_cli_keeps_component_and_packet_identities_distinct(self):
        import validate_packet_native as validator
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary, report = root/'mapac', root/'report.json'
            binary.write_bytes(b'test executable identity')

            def artificial(_, result):
                result['artificial'] = [{}] * 2220

            def controls(_, result):
                result['controls'] = [{}] * 16

            def representatives(_, result, baseline):
                result['representatives'] = [{}] * 15

            arguments = ['validate_packet_native', '--binary', str(binary), '--output', str(report)]
            with patch.object(sys, 'argv', arguments), patch.object(sys, 'platform', 'darwin'), \
                 patch.object(validator, 'component_identity', return_value={'component_sha256': component.CURRENT_SHA256}), \
                 patch.object(validator, 'unchanged') as stable, \
                 patch.object(validator, 'source_digest', return_value='source'), \
                 patch.object(validator, 'artificial', side_effect=artificial), \
                 patch.object(validator, 'controls', side_effect=controls), \
                 patch.object(validator, 'representatives', side_effect=representatives), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(validator.main(), 0)
            self.assertEqual(json.loads(report.read_text())['component_sha256'], component.CURRENT_SHA256)
            stable.assert_called_once()

    def test_drc_off_cli_keeps_component_and_pcm_identities_distinct(self):
        import validate_drc_off as validator
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary, report = root/'mapac', root/'report.json'
            binary.write_bytes(b'test executable identity')

            def capture(binary, bundle, destination, frames, policy):
                output = destination/'native-pcm'
                output.mkdir(parents=True)
                for name in ['replay.json', 'pcm.json']:
                    (output/name).write_text('{}')
                (output/'pcm.f32le').write_bytes(bytes(4))
                return {}

            arguments = ['validate_drc_off', '--binary', str(binary), '--bundle', str(root/'input'),
                         '--report', str(report), '--artifacts', str(root/'captures')]
            with patch.object(sys, 'argv', arguments), \
                 patch.object(validator, 'component_identity', return_value={'component_sha256': component.CURRENT_SHA256}), \
                 patch.object(validator, 'unchanged') as stable, \
                 patch.object(validator, 'source_digest', return_value='source'), \
                 patch.object(validator, 'trace_bundle', side_effect=capture), \
                 patch.object(validator, 'inspect', side_effect=lambda a,b,c,d,policy: dict(policy=policy,passed=True)), \
                 patch('subprocess.check_output', return_value='test\n'), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(validator.main(), 0)
            self.assertEqual(json.loads(report.read_text())['component_sha256'], component.CURRENT_SHA256)
            stable.assert_called_once()


class NativeRepresentativeTests(unittest.TestCase):
    def test_hoa_origin_stop_requires_the_exported_input_precondition(self):
        from validate_cac import pcm_stop
        result = dict(error=dict(operation='SQ access', message=
                      'HOA input must include packet zero to establish sequential state'))
        manifest = dict(complete=True, start_packet=8,
                        file=dict(layout=dict(error=None, value=dict(ambisonic_order=3))))
        self.assertEqual(pcm_stop(result, manifest), 'nonzero_origin')
        invalid = [None, {}, dict(manifest, complete=False), dict(manifest, start_packet=0),
                   dict(manifest, start_packet=True)]
        for order in [None, True, -1]:
            changed = copy.deepcopy(manifest)
            changed['file']['layout']['value']['ambisonic_order'] = order
            invalid.append(changed)
        for value in invalid:
            with self.subTest(manifest=value), self.assertRaises(AssertionError):
                pcm_stop(result, value)
        with self.assertRaises(AssertionError):
            pcm_stop(dict(error=dict(result['error'], message='unexplained access failure')), manifest)

    def test_drc_representatives_accept_supported_multichannel_pcm(self):
        import hashlib
        import validate_drc_native as validator
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            representatives = []
            for channels in [2, 8, 12, 16, 24]:
                for start in [0, 10, 20]:
                    representatives.append(dict(channels=channels, source=str(channels),
                        dump=dict(replay_window=dict(requested_start_packet=start,
                        requested_packets=1, target_raw_start=start*1024, target_raw_end=(start+1)*1024))))
            baseline = root/'baseline.json'
            baseline.write_text(json.dumps(dict(passed=True, representatives=representatives)))

            def command(binary, operation, source, *arguments, allowed=(0,)):
                output = Path(arguments[arguments.index('--out')+1])
                if operation == 'dump':
                    channels = int(source)
                    start = arguments[arguments.index('--start-packet')+1]
                    manifest = dict(complete=True, start_packet=start, file=dict(format=dict(channels=channels),
                        layout=dict(error=None, value=dict(ambisonic_order=3 if channels==16 else None))))
                    output.mkdir()
                    (output/'manifest.json').write_text(json.dumps(manifest))
                    return manifest
                self.assertEqual(operation, 'decode-sq')
                self.assertEqual(allowed, (0, 1))
                manifest = json.loads((Path(source)/'manifest.json').read_text())
                channels = manifest['file']['format']['channels']
                if channels == 16 and manifest['start_packet'] > 0:
                    return dict(error=dict(operation='SQ access', message=
                                'HOA input must include packet zero to establish sequential state'))
                output.mkdir()
                raw = bytes(1024 * channels * 4)
                pcm = dict(complete=True, all_finite=True, channels=channels, frames=1024,
                           sha256=hashlib.sha256(raw).hexdigest())
                (output/'pcm.f32le').write_bytes(raw)
                (output/'pcm.json').write_text(json.dumps(pcm))
                return dict(complete=True, native_apis_used=False, saved_frames=1024)

            report = dict(representatives=[], failure_directory=str(root/'failures'))
            with patch.object(validator, 'command', side_effect=command), \
                 patch.object(validator, 'inspect_bundle', side_effect=lambda *a, **kw:
                              (dict(pcm_metrics=dict(passed=True)), None, None)), \
                 contextlib.redirect_stderr(io.StringIO()):
                validator.representatives(root/'mapac', report, baseline)
            self.assertEqual(len(report['representatives']), 15)
            self.assertTrue(all(row['passed'] for row in report['representatives']))
            self.assertEqual(sum(row.get('independent_pcm_complete', False) for row in report['representatives']), 10)
            self.assertEqual(sum(row.get('stop_reason') == 'nonzero_origin' for row in report['representatives']), 2)


if __name__ == '__main__':
    unittest.main()
