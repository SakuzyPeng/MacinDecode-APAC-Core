"""Container/PCM proof must fail rather than accepting missing or damaged evidence."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from validate_mp4_native import check_input,check_media_windows,media,same_pcm

class Mp4NativeTests(unittest.TestCase):
    def test_all_four_qualified_windows_are_accepted_in_any_order(self):
        windows=[dict(kind=kind,passed=True) for kind in ('head','middle','refresh','tail')]
        check_media_windows(windows)
        check_media_windows(windows[::-1])

    def test_incomplete_reference_windows_fail_before_opening_any_source(self):
        windows=[dict(kind=kind,passed=True) for kind in ('head','middle','refresh','tail')]
        invalid=[None,[],windows[:-1],windows+[windows[0]],windows[:-1]+[windows[0]],
                 [dict(w,kind='unknown') if w['kind']=='tail' else w for w in windows],
                 [dict(w,passed=False) if w['kind']=='tail' else w for w in windows],
                 [dict(kind=w['kind']) if w['kind']=='tail' else w for w in windows]]
        baseline=dict(passed=True,media=[dict(source_sha256=f'{i:064x}',passed=True,windows=windows) for i in range(13)])
        with tempfile.TemporaryDirectory(prefix='apac-mp4-reference-') as d:
            root=Path(d);collection=root/'collection.json';prior=root/'prior.json'
            collection.write_text(json.dumps(dict(configs=[dict(sources=[
                dict(source=str(root/f'{i}.m4a'),format=dict(channels=8)) for i in range(13)
            ])])),encoding='utf-8')
            for bad_windows in invalid:
                with self.subTest(windows=bad_windows):
                    bad=copy.deepcopy(baseline)
                    # A bad final source must also fail before any earlier source is opened.
                    if bad_windows is None:bad['media'][-1].pop('windows')
                    else:bad['media'][-1]['windows']=bad_windows
                    prior.write_text(json.dumps(bad),encoding='utf-8')
                    with patch('validate_mp4_native.sha256_file') as hashed, \
                         patch('validate_mp4_native.verify') as verified, \
                         patch('validate_mp4_native.command') as command:
                        with self.assertRaisesRegex(AssertionError,'media window'):
                            media(root/'mapac',{},collection,prior)
                        hashed.assert_not_called();verified.assert_not_called();command.assert_not_called()

    def test_pcm_byte_length_and_any_channel_difference_are_fatal(self):
        raw=bytes(8*4*1024);same_pcm(raw,raw,1024,8)
        for bad in (raw[:-1],raw[:-1]+b'\x01'):
            with self.assertRaises(AssertionError):same_pcm(raw,bad,1024,8)
    def test_container_identity_and_timeline_cannot_be_omitted(self):
        evidence=dict(audio_sha256='a',packets_sha256='p',cookie_sha256='c',table={'priming_frames':7},packets=8,channels=8,rate=48000,layout_tag=8388616)
        r=dict(input=dict(kind='mp4',profile='apac-mp4-input-v1',consistency_verified=True,audio_sha256='a',packets_sha256='p',cookie_sha256='c',packet_table=evidence['table'],packet_count=8),
               pcm=dict(channels=8,sample_rate=48000,layout=dict(value=dict(tag=8388616))),integrity_checked_packets=8,drc_processing='off',loudness_normalization='off',complete=True,experimental=True,native_apis_used=False)
        check_input(r,evidence)
        for key in r['input']:
            bad=copy.deepcopy(r);bad['input'][key]=None
            with self.assertRaises(AssertionError):check_input(bad,evidence)
        for key in ('integrity_checked_packets','drc_processing','complete','native_apis_used'):
            bad=copy.deepcopy(r);bad[key]=True if key=='native_apis_used' else None
            with self.assertRaises(AssertionError):check_input(bad,evidence)
