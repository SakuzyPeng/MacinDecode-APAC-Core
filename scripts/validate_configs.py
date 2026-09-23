#!/usr/bin/env python3
"""Validate pure-Rust cookie parsing against a local collection and generated controls."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from validate import caf_payload, require, write_json


def command(binary, *args, allowed=(0,)):
    result = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, timeout=120)
    require(result.returncode in allowed, f"{args[0]} exited {result.returncode}: {result.stderr[-3000:]}")
    return result.returncode, json.loads(result.stdout or result.stderr)


def check_coverage(cookie, parsed):
    require(parsed["cookie_sha256"] == hashlib.sha256(cookie).hexdigest(), "parser hash mismatch")
    spans = [(f["bit_offset"], f["bit_offset"] + f["bit_length"]) for f in parsed["fields"] if f["bit_length"]]
    for unknown in parsed["unknown_ranges"]:
        start, size = unknown["bit_offset"], unknown["bit_length"]
        require(unknown["first_byte_skip_bits"] == start % 8, "unknown range alignment mismatch")
        require(unknown["raw_hex"] == cookie[start // 8:(start + size + 7) // 8].hex(), "unknown bytes were not preserved")
        spans.append((start, start + size))
    position = 0
    for start, end in sorted(spans):
        require(0 <= start <= position <= len(cookie) * 8 and start <= end <= len(cookie) * 8, "gap/invalid field coverage")
        position = max(position, end)
    require(position == len(cookie) * 8, "unaccounted cookie bits")
    if parsed["status"] == "complete":
        require(not parsed["unknown_ranges"], "complete result contains unknown ranges")
        position = 0
        for field in parsed["fields"]:
            require(field["bit_offset"] == position, "overlapping or missing complete fields")
            position += field["bit_length"]


def check_metadata(parsed, info):
    derived = parsed["derived"]
    for field, expected in [("channels", info["format"]["channels"]),
                            ("sample_rate_hz", info["format"]["sample_rate"]),
                            ("frame_samples", info["format"]["frames_per_packet"])]:
        require(derived.get(field) == expected, f"cookie-derived {field} disagrees with AudioToolbox")
    layout = derived.get("components[0].layout_tag")
    if layout is not None and info["layout"]["value"] is not None:
        require(layout == info["layout"]["value"]["tag"], "cookie-derived layout disagrees with AudioToolbox")
        for field in ["ambisonic_order", "ambisonic_channel_order", "ambisonic_normalization"]:
            if f"components[0].{field}" in derived:
                require(derived[f"components[0].{field}"] == info["layout"]["value"][field],
                        f"cookie-derived {field} disagrees with AudioToolbox")


def baseline_entries(path, hashes):
    baseline = json.loads(path.read_text())
    require(baseline["schema_version"] == 1 and baseline["passed"] and not baseline["failures"],
            "baseline is not a successful schema-v1 report")
    entries = {item["sha256"]: item["report"] for item in baseline["configurations"]}
    require(len(entries) == len(baseline["configurations"]) and set(entries) == hashes,
            "baseline and collection must contain the same unique hashes")
    return entries


def drc_baseline(path, hashes):
    entries = baseline_entries(path, hashes)
    targets = set()
    for sha, report in entries.items():
        fields = [f for f in report["fields"] if f["name"] == "ancillary.loudness_drc_present"]
        unknown = report["unknown_ranges"]
        if (report["status"] == "partial" and len(fields) == 1 and fields[0]["value"] is True and fields[0]["bit_length"] == 1
                and len(unknown) == 1 and unknown[0]["bit_offset"] == fields[0]["bit_offset"] + 1
                and unknown[0]["reason"] == "ancillary.loudness_drc_present: present branch is not implemented"):
            targets.add(sha)
    require(len(entries) == 64 and len(targets) == 50, "DRC milestone requires 64 baseline configs and 50 DRC targets")
    complete = {sha for sha, report in entries.items() if report["status"] == "complete"}
    remaining = set(entries) - targets - complete
    require(len(complete) == 12 and len(remaining) == 2, "unexpected DRC baseline support groups")
    require(all(any(d["message"] == "ASC type 2 is not implemented" for d in entries[sha]["diagnostics"])
                for sha in remaining), "remaining baseline configurations must be HOA ASC type 2")
    return entries, targets


def hoa_baseline(path, hashes):
    entries = baseline_entries(path, hashes)
    targets = set()
    for sha, report in entries.items():
        fields = [f for f in report["fields"] if re.fullmatch(r"components\[\d+\]\.type", f["name"])
                  and f["value"] == 2 and f["bit_length"] == 3]
        unknown = report["unknown_ranges"]
        if (report["status"] == "partial" and len(unknown) == 1
                and unknown[0]["reason"] == "ASC type 2 is not implemented"
                and any(unknown[0]["bit_offset"] == f["bit_offset"] + 3 for f in fields)):
            targets.add(sha)
    complete = {sha for sha, report in entries.items() if report["status"] == "complete"}
    require(len(entries) == 64 and len(complete) == 62 and len(targets) == 2,
            "HOA milestone requires 62 complete baseline configs and two ASC type 2 targets")
    return entries, targets


def check_progress(sha, previous, parsed, code, targets):
    if sha in targets or previous["status"] == "complete":
        require(parsed["status"] == "complete" and code == 0, "milestone target or previously complete cookie is incomplete")
    else:
        require((parsed["status"] == "complete" and code == 0)
                or (parsed["status"] == "partial" and code == 2 and parsed["diagnostics"] == previous["diagnostics"]),
                "non-target baseline cookie neither preserved its stop nor became complete")
    current = {f["name"]: f for f in parsed["fields"]}
    require(all(json.dumps(current.get(f["name"]), sort_keys=True) == json.dumps(f, sort_keys=True)
                for f in previous["fields"]), "previously parsed fields changed")
    require(all(json.dumps(parsed.get("derived", {}).get(k), sort_keys=True) == json.dumps(v, sort_keys=True)
                for k, v in previous.get("derived", {}).items()),
            "previously derived values changed")


def check_drc_setting(manifest):
    requested = manifest["requested"]["drc_configuration"]
    if requested is not None:
        value = {"none": 0, "music": 1, "speech": 2, "movie": 3, "capture": 4}[requested]
        actual = manifest["actual_encoder_settings"]["cdrc"]
        require(actual["value"] == value and actual["error"] is None
                and manifest["drc_configuration_verified"] is True, "DRC setting was not verified by encoder readback")


def fixtures(binary):
    variants = [(name, parameters, "sine") for name, parameters in [
        ("baseline", []),
        ("rate-44100", ["--sample-rate", "44100"]),
        ("rate-32000", ["--sample-rate", "32000"]),
        ("mono", ["--layout", "mono"]),
        ("surround71", ["--layout", "surround71"]),
        ("surround714", ["--layout", "surround714"]),
        ("hoa3", ["--layout", "hoa3"]),
        ("surround222", ["--layout", "surround222"]),
        ("quality-96", ["--quality", "96"]),
        ("bitrate-256000", ["--bitrate", "256000"]),
        ("drc-default", []),
        *[(f"drc-{mode}", ["--drc-configuration", mode])
          for mode in ["none", "music", "speech", "movie", "capture"]],
    ]]
    variants.extend([
        ("hoa1", ["--layout", "hoa1"], "sine"),
        ("hoa2", ["--layout", "hoa2"], "sine"),
        ("hoa3-drc-none", ["--layout", "hoa3", "--drc-configuration", "none"], "sine"),
        ("hoa3-drc-capture", ["--layout", "hoa3", "--drc-configuration", "capture"], "sine"),
        ("hoa3-rate-44100", ["--layout", "hoa3", "--sample-rate", "44100"], "sine"),
        ("hoa3-quality-96", ["--layout", "hoa3", "--quality", "96"], "sine"),
        ("hoa3-channel-solo", ["--layout", "hoa3"], "channel-solo"),
    ])
    results = []
    baselines = {}
    for name, parameters, signal in variants:
        print(f"configuration control: {name}", flush=True)
        result = {"name": name, "parameters": parameters, "signal": signal, "passed": False}
        results.append(result)
        try:
            with tempfile.TemporaryDirectory(prefix="apac-config-control-") as tmp:
                root = Path(tmp)
                command(binary, "fixture", "--out", root / "fixture", "--signals", signal, "--duration", "0.125", *parameters)
                case = root / "fixture" / signal
                manifest = json.loads((case / "manifest.json").read_text())
                result.update({"requested": manifest["requested"], "actual_encoder_settings": manifest["actual_encoder_settings"],
                               "drc_configuration_verified": manifest["drc_configuration_verified"]})
                check_drc_setting(manifest)
                media = case / "encoded.caf"
                _, info = command(binary, "inspect", media)
                cookie = caf_payload(media, b"kuki")
                raw = root / "cookie.bin"
                raw.write_bytes(cookie)
                code, parsed = command(binary, "parse-cookie", raw, allowed=(0, 2))
                result.update({"status": parsed["status"], "exit_code": code, "cookie_bytes": len(cookie),
                               "cookie_sha256": parsed["cookie_sha256"], "derived": parsed["derived"],
                               "diagnostics": parsed["diagnostics"], "unknown_ranges": parsed["unknown_ranges"]})
                check_coverage(cookie, parsed)
                check_metadata(parsed, info)
                require(parsed["status"] == "complete" and code == 0, "control configuration is not completely parsed")
                values = {f["name"]: f["value"] for f in parsed["fields"]}
                if name.startswith("drc-") or "-drc-" in name:
                    require(values["ancillary.loudness_drc_present"] == (not name.endswith("-none")), "DRC presence control disagrees")
                if name.startswith("hoa"):
                    order = {"hoa1": 1, "hoa2": 2}.get(name, 3)
                    derived = parsed["derived"]
                    require(derived["components[0].hoa.order"] == order, "HOA order differs from encoder request")
                    require(derived["components[0].hoa.coefficient_count"] == (order + 1)**2,
                            "HOA coefficient count differs from encoder request")
                baselines[name] = values
                baseline_name = "hoa3" if name.startswith("hoa") and name != "hoa3" else "baseline"
                require(baseline_name in baselines, "comparison baseline control did not succeed")
                baseline = baselines[baseline_name]
                changed = {key: {"baseline": baseline.get(key), "variant": values.get(key)}
                           for key in sorted(baseline.keys() | values.keys()) if baseline.get(key) != values.get(key)}
                result.update({"comparison_baseline": baseline_name, "changed_confirmed_fields": changed, "passed": True})
        except Exception as error:
            result["error"] = str(error)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, default=Path("artifacts/configs-v1/index.json"))
    parser.add_argument("--binary", type=Path, default=Path("target/debug/apac-tool"))
    parser.add_argument("--output", type=Path, default=Path("reports/config-validation.json"))
    parser.add_argument("--expected-configs", type=int, default=64)
    parser.add_argument("--expected-targets", type=int, default=3)
    parser.add_argument("--skip-fixtures", action="store_true", help="Skip macOS-dependent encoder controls")
    milestone = parser.add_mutually_exclusive_group()
    milestone.add_argument("--drc-baseline", type=Path, help="Require the 50 DRC targets from a phase-2 report to become complete")
    milestone.add_argument("--hoa-baseline", type=Path, help="Require the two HOA targets from a phase-3 report and all 64 configs to be complete")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite the validation report")
    binary = args.binary.resolve()
    collection = json.loads(args.collection.read_text())
    require(collection["schema_version"] == 1 and collection["complete"] and collection["all_collected"], "collection is incomplete")
    require(len(collection["configs"]) == args.expected_configs, "unexpected configuration count")
    previous, drc_targets, hoa_targets = {}, set(), set()
    hashes = {item["sha256"] for item in collection["configs"]}
    if args.drc_baseline:
        previous, drc_targets = drc_baseline(args.drc_baseline, hashes)
    elif args.hoa_baseline:
        previous, hoa_targets = hoa_baseline(args.hoa_baseline, hashes)
    results = []
    failures = []
    targets = 0
    for item in collection["configs"]:
        filename = args.collection.parent / item["cookie_file"]
        target = item["cookie_bytes"] == 54 and item["sources"][0]["format"]["channels"] == 8
        targets += int(target)
        result = {"sha256": item["sha256"], "bytes": item["cookie_bytes"], "source_count": len(item["sources"]),
                  "first_milestone_target": target, "drc_target": item["sha256"] in drc_targets,
                  "hoa_target": item["sha256"] in hoa_targets, "passed": False}
        results.append(result)
        try:
            cookie = filename.read_bytes()
            require(hashlib.sha256(cookie).hexdigest() == item["sha256"], "collection hash mismatch")
            code, parsed = command(binary, "parse-cookie", filename, allowed=(0, 1, 2))
            result.update({"exit_code": code, "report": parsed})
            require(code != 1, f"valid corpus cookie rejected: {parsed}")
            check_coverage(cookie, parsed)
            for source in item["sources"]:
                check_metadata(parsed, source)
            if target:
                require(parsed["status"] == "complete" and code == 0, "target configuration not completely parsed")
            if previous:
                prior = previous[item["sha256"]]
                result["baseline_status"] = prior["status"]
                check_progress(item["sha256"], prior, parsed, code, drc_targets | hoa_targets)
            drc_fields = [f for f in parsed["fields"] if f["name"].startswith("ancillary.loudness_drc.")]
            if drc_fields:
                start = drc_fields[0]["bit_offset"]
                end = drc_fields[-1]["bit_offset"] + drc_fields[-1]["bit_length"]
                result["drc_bit_range"] = {"bit_offset": start, "bit_length": end - start}
            hoa_ranges = {}
            for field in parsed["fields"]:
                if ".hoa." in field["name"]:
                    component = field["name"].split(".hoa.")[0]
                    span = hoa_ranges.setdefault(component, {"bit_offset": field["bit_offset"], "bit_length": 0})
                    span["bit_length"] = field["bit_offset"] + field["bit_length"] - span["bit_offset"]
            if hoa_ranges:
                result["hoa_bit_ranges"] = hoa_ranges
            result["passed"] = True
        except Exception as error:
            result["error"] = str(error)
            failures.append({"sha256": item["sha256"], "error": str(error)})
    require(targets == args.expected_targets, "unexpected first-milestone target count")
    controls = []
    if not args.skip_fixtures:
        try:
            controls = fixtures(binary)
            failures.extend({"stage": "controlled encoding", "name": c["name"], "error": c["error"]}
                            for c in controls if not c["passed"])
        except Exception as error:
            failures.append({"stage": "controlled encoding", "error": str(error)})
    counts = Counter(r.get("report", {}).get("status", "error") for r in results)
    if args.drc_baseline and (counts["complete"] < 62 or counts["complete"] + counts["partial"] != 64):
        failures.append({"stage": "DRC coverage", "error": "expected at least 62 complete; only the two HOA configs may remain partial"})
    if args.hoa_baseline and counts != Counter({"complete": 64}):
        failures.append({"stage": "HOA coverage", "error": "expected all 64 configurations to be complete"})
    report = {"schema_version": 1, "finished_utc": datetime.now(timezone.utc).isoformat(),
              "code_baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "tested_worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
              "collection": str(args.collection), "source_records": collection["source_records"],
              "configuration_count": len(collection["configs"]), "status_counts": dict(counts),
              "target_configurations": targets, "configurations": results, "controls": controls,
              "drc_baseline": str(args.drc_baseline) if args.drc_baseline else None,
              "drc_baseline_sha256": hashlib.sha256(args.drc_baseline.read_bytes()).hexdigest() if args.drc_baseline else None,
              "drc_target_count": len(drc_targets), "fixtures_executed": not args.skip_fixtures,
              "hoa_baseline": str(args.hoa_baseline) if args.hoa_baseline else None,
              "hoa_baseline_sha256": hashlib.sha256(args.hoa_baseline.read_bytes()).hexdigest() if args.hoa_baseline else None,
              "hoa_target_count": len(hoa_targets),
              "failures": failures, "passed": not failures,
              "scope": "complete means implemented cookie syntax coverage; unnamed numeric fields are not assigned speculative operational meanings"}
    write_json(args.output, report)
    print(json.dumps({"passed": report["passed"], "status_counts": counts, "target_configurations": targets,
                      "controls": len(controls), "failures": failures}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
