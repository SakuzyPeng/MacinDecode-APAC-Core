"""Regression tests for strict configuration milestone acceptance, independent of macOS."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from validate_configs import check_coverage, check_drc_setting, check_progress, drc_baseline, hoa_baseline, main


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
        complete["fields"][0]["bit_offset"] -= 1
        complete["fields"][0]["value"] = 1  # JSON true and 1 are different schema values.
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


class HoaAcceptanceTests(unittest.TestCase):
    @staticmethod
    def baseline():
        baseline = DrcAcceptanceTests.baseline()
        for i, item in enumerate(baseline["configurations"]):
            report = item["report"]
            if i < 62:
                report.update(status="complete", unknown_ranges=[], diagnostics=[])
            else:
                report["fields"] = [{"name": "components[0].type", "bit_offset": 163, "bit_length": 3, "value": 2}]
                report["unknown_ranges"] = [{"bit_offset": 166, "bit_length": 18, "reason": "ASC type 2 is not implemented"}]
        return baseline

    def test_hoa_targets_need_the_exact_component_stop_and_hash_set(self):
        baseline = self.baseline()
        hashes = {c["sha256"] for c in baseline["configurations"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(json.dumps(baseline))
            _, targets = hoa_baseline(path, hashes)
            self.assertEqual(targets, {f"{i:064x}" for i in (62, 63)})
            with self.assertRaises(AssertionError):
                hoa_baseline(path, hashes - {next(iter(hashes))})
            baseline["configurations"][-1]["report"]["fields"][0]["name"] = "components[0].tce[0].type"
            path.write_text(json.dumps(baseline))
            with self.assertRaises(AssertionError):
                hoa_baseline(path, hashes)

    def test_drc_milestone_accepts_hoa_completion_but_hoa_milestone_rejects_partial(self):
        previous = self.baseline()["configurations"][-1]["report"]
        completed = copy.deepcopy(previous)
        completed.update(status="complete", unknown_ranges=[], diagnostics=[])
        check_progress("hoa", previous, completed, 0, {"drc"})
        check_progress("hoa", previous, completed, 0, {"hoa"})
        with self.assertRaises(AssertionError):
            check_progress("hoa", previous, previous, 2, {"hoa"})
        old = {"status": "complete", "fields": [], "derived": {"channels": 16}}
        with self.assertRaises(AssertionError):
            check_progress("old", old, {**old, "derived": {"channels": 9}}, 0, {"hoa"})

    def test_incomplete_targets_produce_a_failed_report_with_all_file_results(self):
        # Simulate parser reports to exercise the acceptance runner, not APAC syntax.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configs, reports = [], []
            by_path = {}
            for i in range(64):
                data = bytes([i if i < 62 else i + 32])
                sha = hashlib.sha256(data).hexdigest()
                filename = root / f"{i}.bin"
                filename.write_bytes(data)
                derived = {"channels": 2, "sample_rate_hz": 48000, "frame_samples": 1024}
                report = {"cookie_sha256": sha, "status": "complete", "derived": derived,
                          "fields": [{"name": "byte", "bit_offset": 0, "bit_length": 8, "value": data[0]}],
                          "unknown_ranges": [], "diagnostics": []}
                if i >= 62:
                    report.update(status="partial", fields=[{"name": "components[0].type", "bit_offset": 0,
                                                             "bit_length": 3, "value": 2}],
                                  unknown_ranges=[{"bit_offset": 3, "bit_length": 5, "first_byte_skip_bits": 3,
                                                   "raw_hex": data.hex(), "reason": "ASC type 2 is not implemented"}])
                source = {"format": {"channels": 2, "sample_rate": 48000, "frames_per_packet": 1024},
                          "layout": {"value": None}}
                configs.append({"sha256": sha, "cookie_file": filename.name, "cookie_bytes": 1, "sources": [source]})
                reports.append({"sha256": sha, "report": report})
                by_path[str(filename)] = (0 if i < 62 else 2, report)
            collection = root / "index.json"
            collection.write_text(json.dumps({"schema_version": 1, "complete": True, "all_collected": True,
                                              "source_records": 64, "configs": configs}))
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({"schema_version": 1, "passed": True, "failures": [], "configurations": reports}))
            output = root / "result.json"
            arguments = ["validate_configs", "--collection", str(collection), "--hoa-baseline", str(baseline),
                         "--output", str(output), "--skip-fixtures", "--expected-targets", "0"]
            with patch("sys.argv", arguments), patch("validate_configs.command", side_effect=lambda _, __, f, **kw: by_path[str(f)]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 1)
            result = json.loads(output.read_text())
            self.assertFalse(result["passed"])
            self.assertEqual(len(result["configurations"]), 64)
            failed = [c for c in result["configurations"] if not c["passed"]]
            self.assertEqual(len(failed), 2)
            self.assertTrue(all(c["report"]["unknown_ranges"] for c in failed))

    def test_milestone_flags_are_mutually_exclusive(self):
        with patch("sys.argv", ["validate_configs", "--hoa-baseline", "hoa.json", "--drc-baseline", "drc.json"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
