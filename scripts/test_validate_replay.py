"""Acceptance guard tests: a comparison must not hide range or drain errors."""
import copy
import unittest

from validate_replay import check_accounting


class ReplayAccountingTests(unittest.TestCase):
    @staticmethod
    def sample():
        return ({"complete": True, "original_source_accessed": False, "saved_frames": 14,
                 "range": {"frames": 14, "start_frame": 0, "drain_to_eof": True},
                 "produced_raw_frames": 20, "discarded_before_frames": 3, "discarded_after_frames": 3,
                 "consumed_packet_frames": 20, "eof_sent": True, "eof_drained": True,
                 "consumed_packets": 5, "stored_packets": 5, "stored_raw_start": 0, "stored_raw_end": 20},
                {"complete": True, "all_finite": True, "frames": 14, "start_frame": 0})

    def test_valid_nonstandard_priming_and_padding_accounting(self):
        report, pcm = self.sample()
        check_accounting(report, pcm)

    def test_partial_output_wrong_origin_and_missing_drain_fail(self):
        report, pcm = self.sample()
        for field, value in [("saved_frames", 13), ("eof_sent", False), ("eof_drained", False),
                             ("consumed_packets", 4), ("produced_raw_frames", 19),
                             ("consumed_packet_frames", 16), ("original_source_accessed", True)]:
            changed = copy.deepcopy(report)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                check_accounting(changed, pcm)
        for field, value in [("start_frame", 1), ("all_finite", False), ("complete", False)]:
            changed = copy.deepcopy(pcm)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError):
                check_accounting(report, changed)


if __name__ == "__main__":
    unittest.main()
