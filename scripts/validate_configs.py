#!/usr/bin/env python3
"""Validate pure-Rust cookie parsing against a local collection and generated controls."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
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


def fixtures(binary):
    variants = [
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
    ]
    results = []
    baseline = None
    for name, parameters in variants:
        print(f"configuration control: {name}", flush=True)
        with tempfile.TemporaryDirectory(prefix="apac-config-control-") as tmp:
            root = Path(tmp)
            command(binary, "fixture", "--out", root / "fixture", "--signals", "sine", "--duration", "0.125", *parameters)
            media = root / "fixture/sine/encoded.caf"
            _, info = command(binary, "inspect", media)
            cookie = caf_payload(media, b"kuki")
            raw = root / "cookie.bin"
            raw.write_bytes(cookie)
            code, parsed = command(binary, "parse-cookie", raw, allowed=(0, 2))
            check_coverage(cookie, parsed)
            check_metadata(parsed, info)
            values = {f["name"]: f["value"] for f in parsed["fields"]}
            if baseline is None:
                baseline = values
            changed = {key: {"baseline": baseline.get(key), "variant": value}
                       for key, value in values.items() if baseline.get(key) != value}
            results.append({"name": name, "parameters": parameters, "status": parsed["status"], "exit_code": code,
                            "cookie_bytes": len(cookie), "cookie_sha256": parsed["cookie_sha256"],
                            "derived": parsed["derived"], "changed_confirmed_fields": changed,
                            "diagnostics": parsed["diagnostics"], "passed": True})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, default=Path("artifacts/configs-v1/index.json"))
    parser.add_argument("--binary", type=Path, default=Path("target/debug/apac-tool"))
    parser.add_argument("--output", type=Path, default=Path("reports/config-validation.json"))
    parser.add_argument("--expected-configs", type=int, default=64)
    parser.add_argument("--expected-targets", type=int, default=3)
    parser.add_argument("--skip-fixtures", action="store_true", help="Skip macOS-dependent encoder controls")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite the validation report")
    binary = args.binary.resolve()
    collection = json.loads(args.collection.read_text())
    require(collection["schema_version"] == 1 and collection["complete"] and collection["all_collected"], "collection is incomplete")
    require(len(collection["configs"]) == args.expected_configs, "unexpected configuration count")
    results = []
    failures = []
    targets = 0
    for item in collection["configs"]:
        filename = args.collection.parent / item["cookie_file"]
        cookie = filename.read_bytes()
        require(hashlib.sha256(cookie).hexdigest() == item["sha256"], "collection hash mismatch")
        target = len(cookie) == 54 and item["sources"][0]["format"]["channels"] == 8
        targets += int(target)
        try:
            code, parsed = command(binary, "parse-cookie", filename, allowed=(0, 1, 2))
            require(code != 1, f"valid corpus cookie rejected: {parsed}")
            check_coverage(cookie, parsed)
            for source in item["sources"]:
                check_metadata(parsed, source)
            if target:
                require(parsed["status"] == "complete" and code == 0, "target configuration not completely parsed")
            results.append({"sha256": item["sha256"], "bytes": len(cookie), "source_count": len(item["sources"]),
                            "first_milestone_target": target, "exit_code": code, "report": parsed, "passed": True})
        except Exception as error:
            failures.append({"sha256": item["sha256"], "error": str(error)})
    require(targets == args.expected_targets, "unexpected first-milestone target count")
    controls = []
    if not args.skip_fixtures:
        try:
            controls = fixtures(binary)
        except Exception as error:
            failures.append({"stage": "controlled encoding", "error": str(error)})
    counts = Counter(r["report"]["status"] for r in results)
    report = {"schema_version": 1, "finished_utc": datetime.now(timezone.utc).isoformat(),
              "code_baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "tested_worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
              "collection": str(args.collection), "source_records": collection["source_records"],
              "configuration_count": len(collection["configs"]), "status_counts": dict(counts),
              "target_configurations": targets, "configurations": results, "controls": controls,
              "failures": failures, "passed": not failures,
              "scope": "complete means implemented cookie syntax coverage; unnamed numeric fields are not assigned speculative operational meanings"}
    write_json(args.output, report)
    print(json.dumps({"passed": report["passed"], "status_counts": counts, "target_configurations": targets,
                      "controls": len(controls), "failures": failures}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
