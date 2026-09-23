"""Regression tests for strict DRC milestone acceptance, independent of macOS."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from validate_configs import check_coverage, check_drc_setting, check_progress, drc_baseline


class DrcAcceptanceTests(unittest.TestCase):
    @staticmethod
    def baseline():
        entries = []
        for i in range(64):
            sha = f"{i:064x}"
            field = {"name": "ancillary.loudness_drc_present", "bit_offset": 200,
                     "bit_length": 1, "value": i < 50}
            drc = {"bit_offset": 201, "bit_length": 7,
                   "reason": "ancillary.loudness_drc_present: present branch is not implemented"}
            report = {"status": "partial" if i < 50 or i >= 62 else "complete", "fields": [field],
                      "unknown_ranges": [drc] if i < 50 else [],
                      "diagnostics": [{"bit_offset": 166, "message": "ASC type 2 is not implemented"}] if i >= 62 else []}
            entries.append({"sha256": sha, "report": report})
        return {"schema_version": 1, "passed": True, "failures": [], "configurations": entries}

    def test_targets_are_selected_by_prior_stop_and_hash_not_cookie_size(self):
        baseline = self.baseline()
        hashes = {c["sha256"] for c in baseline["configurations"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(json.dumps(baseline))
            entries, targets = drc_baseline(path, hashes)
            self.assertEqual(len(entries), 64)
            self.assertEqual(targets, {f"{i:064x}" for i in range(50)})
            with self.assertRaises(AssertionError):
                drc_baseline(path, hashes - {next(iter(hashes))})
            baseline["configurations"][0]["report"]["unknown_ranges"][0]["bit_offset"] += 1
            path.write_text(json.dumps(baseline))
            with self.assertRaises(AssertionError):
                drc_baseline(path, hashes)

    def test_partial_targets_field_regressions_and_hoa_changes_fail(self):
        previous = self.baseline()["configurations"][0]["report"]
        with self.assertRaises(AssertionError):
            check_progress("target", previous, previous, 2, {"target"})
        complete = copy.deepcopy(previous)
        complete.update(status="complete", unknown_ranges=[])
        check_progress("target", previous, complete, 0, {"target"})
        complete["fields"][0]["bit_offset"] += 1
        with self.assertRaises(AssertionError):
            check_progress("target", previous, complete, 0, {"target"})
        hoa = self.baseline()["configurations"][-1]["report"]
        changed = copy.deepcopy(hoa)
        changed["diagnostics"] = []
        with self.assertRaises(AssertionError):
            check_progress("hoa", hoa, changed, 2, {"target"})

    def test_unavailable_mismatched_or_failed_encoder_readback_is_not_evidence(self):
        manifest = {"requested": {"drc_configuration": "music"}, "drc_configuration_verified": True,
                    "actual_encoder_settings": {"cdrc": {"value": 1, "error": None}}}
        check_drc_setting(manifest)
        for prop in [{"value": None, "error": {"os_status": -50}}, {"value": 4, "error": None},
                     {"value": 1, "error": {"os_status": -50}}]:
            changed = copy.deepcopy(manifest)
            changed["actual_encoder_settings"]["cdrc"] = prop
            with self.assertRaises(AssertionError):
                check_drc_setting(changed)
        manifest["drc_configuration_verified"] = False
        with self.assertRaises(AssertionError):
            check_drc_setting(manifest)

    def test_complete_cannot_hide_unexplained_bits(self):
        import hashlib
        data = b"\x00"
        parsed = {"cookie_sha256": hashlib.sha256(data).hexdigest(), "status": "complete", "fields": [],
                  "unknown_ranges": [{"bit_offset": 0, "bit_length": 8, "first_byte_skip_bits": 0, "raw_hex": "00"}]}
        with self.assertRaises(AssertionError):
            check_coverage(data, parsed)


if __name__ == "__main__":
    unittest.main()
