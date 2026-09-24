"""Synthetic checks for the prefix oracle and phase-six acceptance gates."""
import copy
import hashlib
import unittest

from validate_frames import PREFIX, check_report, control_specs, prefix_oracle, check_native_boundaries


def report_for(data):
    fields, derived, stop, reason, payload, opaque = prefix_oracle(data)
    return {"schema_version": 1, "status": "partial", "packet_bytes": len(data),
            "packet_sha256": hashlib.sha256(data).hexdigest(), "component_end_bit_offset": None,
            "prefix_complete": reason != "lrvq_prefix_deferred", "fields": [{"name": name, "bit_offset": start,
            "bit_length": width, "value": value} for name, start, width, value in fields],
            "derived": derived, "stop_bit_offset": stop, "stop_reason": reason,
            "payload_bit_offset": payload,
            "unknown_ranges": [{"bit_offset": start, "bit_length": length} for start, length in opaque] + [{"bit_offset": stop, "bit_length": len(data) * 8 - stop, "reason": reason}]}


class PrefixOracleTests(unittest.TestCase):
    def test_hand_constructed_boundaries_and_window_groups(self):
        for raw, stop, reason in [("40", 3, "cpe_absent"), ("6010a5", 12, "sq_left_channel_stream"),
                                  ("6baa80", 17, "sq_left_channel_stream")]:
            data = bytes.fromhex(raw)
            report = report_for(data)
            self.assertEqual((report["stop_bit_offset"], report["stop_reason"]), (stop, reason))
            check_report(data, report, True)
        short = report_for(bytes.fromhex("6baa80"))
        self.assertEqual(short["derived"][PREFIX + ".left_ics.max_sfb"], 14)
        self.assertEqual(short["derived"][PREFIX + ".left_ics.window_groups"], [2, 2, 2, 2])
        self.assertEqual(report_for(bytes.fromhex("6010a5"))["derived"][PREFIX + ".left_ics.max_sfb"], 1)

    def test_lrvq_dispatch_is_a_deferred_branch_not_a_completed_prefix(self):
        data = bytes.fromhex("7025")
        report = report_for(data)
        self.assertFalse(report["prefix_complete"])
        self.assertEqual(report["stop_bit_offset"], 4)
        self.assertEqual(report["stop_reason"], "lrvq_prefix_deferred")
        self.assertEqual(len(report["fields"]), 3)
        with self.assertRaises(AssertionError):
            check_report(data, report, True)

    def test_wrong_boundary_values_types_and_fake_completion_fail(self):
        data = bytes.fromhex("6baa80")
        original = report_for(data)
        for key, value in [("prefix_complete", False), ("status", "complete"), ("component_end_bit_offset", 24),
                           ("stop_bit_offset", 15), ("stop_reason", "cpe_absent"), ("payload_bit_offset", 18),
                           ("packet_sha256", "0" * 64), ("derived", {})]:
            changed = copy.deepcopy(original)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check_report(data, changed, True)
        changed = copy.deepcopy(original)
        changed["fields"][1]["value"] = 1  # JSON true must not become an integer silently.
        with self.assertRaises(AssertionError):
            check_report(data, changed, True)
        changed = copy.deepcopy(original)
        changed["unknown_ranges"][0]["bit_length"] -= 1
        with self.assertRaises(AssertionError):
            check_report(data, changed, True)

    def test_unsupported_results_preserve_common_bit_without_prefix_claim(self):
        data = bytes.fromhex("40")
        report = report_for(data)
        report.update({"fields": report["fields"][:1], "prefix_complete": False,
                       "stop_bit_offset": 2, "stop_reason": "unsupported ASC",
                       "unknown_ranges": [{"bit_offset": 2, "bit_length": 6}]})
        check_report(data, report, False)
        with self.assertRaises(AssertionError):
            check_report(data, report, True)

    def test_control_matrix_keeps_twelve_signal_cases_and_three_single_changes(self):
        controls = control_specs()
        self.assertEqual(len(controls), 15)
        self.assertEqual(len({name for name, _, _ in controls}), 15)
        self.assertEqual(sum("none" in parameters for _, _, parameters in controls), 9)
        self.assertEqual({name for name, _, _ in controls[-3:]}, {"rate-44100", "quality-96", "bitrate-256000"})

    def test_native_boundary_evidence_requires_exact_position_identity_and_ics(self):
        report = report_for(bytes.fromhex("6baa80"))
        event = {"sequence": 0, "packet_sha256": report["packet_sha256"], "native_bit_offset": 17,
                 "boundary": "sq_left_channel_stream", "ics": [{"block_type": 2, "max_sfb": 14,
                 "active_group_count": 4, "active_window_groups": [2, 2, 2, 2]}]}
        trace = {"process_exit_code": 0, "errors": [], "packet_calls": 1, "events": [event]}
        check_native_boundaries([{"report": report}], trace)
        for key, value in [("native_bit_offset", 16), ("packet_sha256", "0" * 64), ("ics", [])]:
            changed = copy.deepcopy(trace)
            changed["events"][0][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check_native_boundaries([{"report": report}], changed)


if __name__ == "__main__":
    unittest.main()
