"""Container/PCM proof must fail rather than accepting missing or damaged evidence."""
import copy,unittest
from validate_mp4_native import check_input,same_pcm

class Mp4NativeTests(unittest.TestCase):
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
