#!/usr/bin/env python3
"""Validate stereo core prefixes against serialization rules and bounded native fixtures."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from validate import require, write_json
from validate_replay import command, sha256_file
from native_frame_trace import trace_bundle


PREFIX = "components[0].tce[0]"


def prefix_oracle(data):
    """Independent oracle using ASP/encoder write order, not the Rust report.

    Explicit preroll byte lengths delimit opaque internal frames. They are not
    interpreted as a component length or as proof of whole-frame completion.
    """
    require(data, "empty packet")
    fields, derived, opaque = [], {}, []
    pos = 0

    def read(name, width, flag=False):
        nonlocal pos
        require(pos + width <= len(data) * 8, "truncated prefix")
        end = pos + width
        word = int.from_bytes(data[pos // 8:(end + 7) // 8], "big")
        value = (word >> ((8 - end % 8) % 8)) & ((1 << width) - 1)
        fields.append((name, pos, width, bool(value) if flag else value))
        pos = end
        return value

    frame_type = read("frame.type_code", 2)
    require(frame_type < 3, "unverified frame type")
    if frame_type == 2:
        require(read("asp.reconfiguration_present", 1, True) == 0, "reconfiguration")
        count = read("asp.preroll_count", 2)
        require(count <= 1, "preroll count")
        if count:
            size = read("asp.preroll.bytes", 16)
            if size == 65535:
                size += read("asp.preroll.extra_bytes", 16)
            require(0 < size <= 4096, "preroll size")
            pad = (-pos) % 8
            if pad:
                require(read("asp.preroll.alignment_padding", pad) == 0, "preroll padding")
            start = pos
            require(start + size * 8 < len(data) * 8, "preroll overruns current frame")
            require(read("asp.preroll.frame_type_code", 2) < 2, "nested/unknown preroll")
            opaque.append((pos, size * 8 - 2))
            pos = start + size * 8
            derived.update({"asp.preroll.start_bit": start, "asp.preroll.end_bit": pos})
    derived["core_frame_start_bit"] = pos
    if not read(PREFIX + ".present", 1, True):
        return fields, derived, pos, "cpe_absent", None, opaque
    lrvq = bool(read(PREFIX + ".coding_type", 1))
    derived["coding_type"] = "lrvq" if lrvq else "sq"
    if lrvq:
        return fields, derived, pos, "lrvq_prefix_deferred", None, opaque
    key = PREFIX + ".left_ics."
    block = read(key + "block_type", 2)
    short = block == 2
    max_sfb = read(key + "max_sfb", 4 if short else 6)
    require(max_sfb <= (14 if short else 49), "invalid max_sfb")
    mask = read(key + "scale_factor_grouping", 7) if short else 0
    groups = [1]
    if short:
        for bit in f"{mask:07b}":
            if bit == "0":
                groups.append(1)
            else:
                groups[-1] += 1
    derived.update({key + "max_sfb": max_sfb, key + "window_groups": groups,
                    key + "active_group_count": len(groups) if max_sfb else 0})
    require(pos < len(data) * 8, "missing payload")
    return fields, derived, pos, "sq_left_channel_stream", pos, opaque


def check_report(data, report, target):
    require(report["schema_version"] == 1 and report["status"] == "partial", "unexpected whole-frame status")
    require(report["packet_bytes"] == len(data) and report["packet_sha256"] == hashlib.sha256(data).hexdigest(), "packet fingerprint mismatch")
    require(report["component_end_bit_offset"] is None, "component end was guessed")
    spans = [(f["bit_offset"], f["bit_length"]) for f in report["fields"]]
    spans += [(r["bit_offset"], r["bit_length"]) for r in report["unknown_ranges"]]
    position = 0
    for offset, length in sorted(spans):
        require(offset == position and length > 0, "gap or overlapping bit ranges")
        position += length
    require(position == len(data) * 8, "unaccounted packet bits")
    require(report["stop_bit_offset"] == report["fields"][-1]["bit_offset"] + report["fields"][-1]["bit_length"], "incorrect stop")
    if target:
        fields, derived, stop, reason, payload, opaque = prefix_oracle(data)
        require([(r["bit_offset"], r["bit_length"]) for r in report["unknown_ranges"]] == opaque + [(stop, len(data) * 8 - stop)], "wrong opaque payload ranges")
        actual = [(f["name"], f["bit_offset"], f["bit_length"], f["value"]) for f in report["fields"]]
        require(actual == fields and all(type(a[3]) is type(b[3]) for a, b in zip(actual, fields)), "prefix fields disagree with serialization oracle")
        require(report["derived"] == derived, "implicit ICS values disagree with serialization oracle")
        require(report["prefix_complete"] and report["stop_bit_offset"] == stop and report["stop_reason"] == reason
                and report["payload_bit_offset"] == payload, "prefix did not reach its confirmed boundary")
    else:
        require(not report["prefix_complete"] and report["payload_bit_offset"] is None, "unsupported configuration claimed prefix completion")
        require(report["stop_bit_offset"] == 2 and len(report["fields"]) == 1
                and report["fields"][0]["value"] == data[0] >> 6, "common frame bit not preserved")


def inspect_bundle(binary, bundle, output, target, record):
    summary = command(binary, "parse-packets", bundle, "--output", output, allowed=(0, 1, 2))
    record["summary"] = summary
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    record["packets"] = rows  # Keep every failure/stop even when a later assertion fails.
    require(summary["complete"] and summary["errors"] == 0, "parse-packets reported an error")
    require(not output.with_name(output.name + ".incomplete").exists(), "unfinished prefix report")
    index = [json.loads(line) for line in (bundle / "packets.jsonl").read_text().splitlines()]
    manifest = json.loads((bundle / "manifest.json").read_text())
    require(len(rows) == len(index) == summary["actual_packets"] == manifest["actual_packets"], "packet coverage mismatch")
    with (bundle / "packets.bin").open("rb") as source:
        for row, packet in zip(rows, index):
            require(row["packet_index"] == packet["packet_index"], "packet number mismatch")
            source.seek(packet["export_offset"])
            data = source.read(packet["bytes"])
            require(row["status"] == "partial", "failed packet in prefix report")
            require(row["report"]["cookie_sha256"] == manifest["file"]["cookie"]["value"]["sha256"], "cookie association mismatch")
            check_report(data, row["report"], target)
    require(summary["whole_frame_complete_packets"] == 0 and summary["exit_code"] == 2, "prefix completion changed whole-frame exit semantics")
    require(summary["all_prefixes_complete"] is target, "target support mismatch")


def check_native_boundaries(rows, trace):
    require(not trace["errors"] and trace["process_exit_code"] == 0, "native trace failed")
    events = trace["events"]
    require(len(events) == len(rows) == trace["packet_calls"], "native trace missed a current-frame boundary")
    for sequence, (row, event) in enumerate(zip(rows, events)):
        report = row["report"]
        require(event["sequence"] == sequence and event["packet_sha256"] == report["packet_sha256"], "native packet identity differs")
        require(event["native_bit_offset"] == report["stop_bit_offset"] and event["boundary"] == report["stop_reason"], "native entry point disagrees with Rust prefix boundary")
        expected_ics = 0 if report["stop_reason"] == "cpe_absent" else 1
        require(len(event["ics"]) == expected_ics, "native trace missed ICS values")
        for side, ics in zip(["left", "right"], event["ics"]):
            key = PREFIX + f".{side}_ics."
            field = next(f for f in report["fields"] if f["name"] == key + "block_type")
            require(field["value"] == ics["block_type"], "native block type mismatch")
            for name in ["max_sfb", "active_group_count"]:
                require(report["derived"][key + name] == ics[name], "native ICS value mismatch: " + name)
            expected_groups = report["derived"][key + "window_groups"] if ics["active_group_count"] else []
            require(expected_groups == ics["active_window_groups"], "native window grouping mismatch")


def native_invalid_sfb(binary, bundle, root):
    """Only mutates a generated, temporary sine bundle after its normal checks."""
    index = [json.loads(line) for line in (bundle / "packets.jsonl").read_text().splitlines()]
    data = bytearray((bundle / "packets.bin").read_bytes())
    packet = next(p for p in index if p["packet_index"] >= 3 and (data[p["export_offset"]] & 252) == 96)
    start = packet["export_offset"]
    for bit in range(6):
        offset = 6 + bit
        mask = 1 << (7 - offset % 8)
        data[start + offset // 8] &= ~mask
        if 50 & (1 << (5 - bit)):
            data[start + offset // 8] |= mask
    packet["sha256"] = hashlib.sha256(data[start:start + packet["bytes"]]).hexdigest()
    (bundle / "packets.bin").write_bytes(data)
    (bundle / "packets.jsonl").write_text("".join(json.dumps(row) + "\n" for row in index))
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["packet_data_sha256"] = hashlib.sha256(data).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    result = command(binary, "parse-packets", bundle, "--output", root / "invalid.jsonl", allowed=(1,))
    rows = [json.loads(line) for line in (root / "invalid.jsonl").read_text().splitlines()]
    bad = next(row for row in rows if row["packet_index"] == packet["packet_index"])
    require(result["errors"] == 1 and bad["error"]["kind"] == "max-sfb" and bad["error"]["bit_offset"] == 6, "wrong syntax failure for altered max_sfb")
    native = command(binary, "replay", bundle, "--out", root / "invalid-replay",
                     "--frames", manifest["file"]["packet_table"]["value"]["valid_frames"], allowed=(0, 1))
    accepted = native.get("complete", False)
    require(accepted or (root / "invalid-replay/.incomplete.json").exists(), "failed replay has no incomplete marker")
    return {"packet_index": packet["packet_index"], "field_bit_offset": 6, "max_sfb": 50,
            "rust_error": bad["error"], "native_accepted": accepted, "native_result": native,
            "scope": "AudioConverter may conceal malformed data; native entry-position snapshots are the boundary oracle"}


def control_specs():
    cases = []
    for signal in ["silence", "impulse", "sine", "sweep", "noise", "channel-solo"]:
        for mode in ["default", "none"]:
            cases.append((signal + "-" + mode, signal, [] if mode == "default" else ["--drc-configuration", "none"]))
    for name, extra in [("rate-44100", ["--sample-rate", "44100"]), ("quality-96", ["--quality", "96"]),
                        ("bitrate-256000", ["--bitrate", "256000"])]:
        cases.append((name, "sine", ["--drc-configuration", "none", *extra]))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("target/debug/apac-tool"))
    parser.add_argument("--replay-baseline", type=Path, default=Path("reports/replay-validation-a76d2f4.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/frame-validation.json"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("generating and replaying acceptance fixtures requires macOS; parse-packets is portable")
    if args.output.exists():
        parser.error("refusing to overwrite report")
    baseline = json.loads(args.replay_baseline.read_text())
    require(baseline["passed"] and len(baseline["representatives"]) == 15, "requires a successful phase-five baseline")
    require(Counter((r["channels"], r["range"]) for r in baseline["representatives"]) == Counter(
        (channels, name) for channels in [2, 8, 12, 16, 24] for name in ["start", "middle", "tail"]), "incorrect representative groups")
    binary = args.binary.resolve()
    report = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "tested_worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
              "system": subprocess.check_output(["sw_vers"], text=True).strip(), "architecture": platform.machine(),
              "component_sha256": sha256_file(Path("/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs")),
              "replay_baseline_sha256": sha256_file(args.replay_baseline), "analysis_architecture": "x86_64",
              "representatives": [], "controls": [], "failures": [],
              "scope": "default-encoder stereo SQ prefixes; prefix_complete is distinct from whole-frame syntax completion and PCM decoding",
              "deferred": ["LRVQ prefixes: stop at coding_type and preserve the remainder"]}
    for base in baseline["representatives"]:
        record = {"channels": base["channels"], "range": base["range"], "source": base["source"], "passed": False}
        report["representatives"].append(record)
        print(f"prefix representative: {record['channels']} channels / {record['range']}", file=sys.stderr, flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix="apac-prefix-source-") as tmp:
                root = Path(tmp)
                window = base["dump"]["replay_window"]
                record["dump"] = command(binary, "dump", base["source"], "--out", root / "packets", "--with-preroll",
                    "--start-packet", window["requested_start_packet"], "--packets", window["requested_packets"])
                inspect_bundle(binary, root / "packets", root / "prefix.jsonl", base["channels"] == 2, record)
                if base["channels"] == 2:
                    record["native_boundaries"] = trace_bundle(binary, root / "packets", root)
                    check_native_boundaries(record["packets"], record["native_boundaries"])
                record["passed"] = True
        except Exception as error:
            record["error"] = str(error)
    for name, signal, extra in control_specs():
        record = {"name": name, "signal": signal, "parameters": extra, "passed": False}
        report["controls"].append(record)
        print(f"prefix control: {name}", file=sys.stderr, flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix="apac-prefix-control-") as tmp:
                root = Path(tmp)
                command(binary, "fixture", "--out", root / "fixture", "--signals", signal,
                        "--duration", "2", "--seed", "1", "--max-output-mib", "8", *extra)
                case = root / "fixture" / signal
                fixture = json.loads((case / "manifest.json").read_text())
                record["encoder"] = {key: fixture[key] for key in ["requested", "actual_encoder_settings", "drc_configuration_verified"]}
                record["cookie"] = fixture["encoded"]["cookie"]
                if "none" in extra:
                    require(fixture["drc_configuration_verified"] is True, "DRC none readback not verified")
                for option, property_name in [("--quality", "cdqu"), ("--bitrate", "brat")]:
                    if option in extra:
                        prop = fixture["actual_encoder_settings"][property_name]
                        require(prop["error"] is None and prop["value"] == int(extra[extra.index(option) + 1]), "encoder parameter not verified")
                bundle = root / "packets"
                record["dump"] = command(binary, "dump", case / "encoded.caf", "--out", bundle, "--with-preroll")
                (case / "encoded.caf").unlink()
                record["original_removed_before_parsing"] = True
                inspect_bundle(binary, bundle, root / "prefix.jsonl", True, record)
                record["native_boundaries"] = trace_bundle(binary, bundle, root)
                check_native_boundaries(record["packets"], record["native_boundaries"])
                frames = fixture["requested"]["frames"]
                record["replay"] = command(binary, "replay", bundle, "--out", root / "replay", "--frames", frames)
                record["comparison"] = command(binary, "compare", case / "reference/pcm.json", root / "replay/pcm.json")
                require(record["comparison"]["passed"] and record["comparison"]["layout_verified"], "fixture reference comparison failed")
                if name == "sine-none":
                    record["invalid_sfb_experiment"] = native_invalid_sfb(binary, bundle, root)
                record["passed"] = True
        except Exception as error:
            record["error"] = str(error)
    stops = Counter()
    blocks = Counter()
    whole = Counter()
    prefixes = 0
    for kind in ["representatives", "controls"]:
        for index, case in enumerate(report[kind]):
            if not case["passed"]:
                report["failures"].append({"stage": kind, "index": index, "error": case["error"]})
            for packet in case.get("packets", []):
                whole[packet["status"]] += 1
                if "report" in packet:
                    frame = packet["report"]
                    prefixes += int(frame["prefix_complete"])
                    stops[frame["stop_reason"]] += 1
                    blocks.update(str(f["value"]) for f in frame["fields"] if f["name"].endswith(".block_type"))
    for required in ["cpe_absent", "sq_left_channel_stream"]:
        if not stops[required]:
            report["failures"].append({"stage": "branch coverage", "error": required + " was not observed"})
    report.update({"prefix_complete_packets": prefixes, "whole_frame_status_counts": dict(whole),
                   "stop_counts": dict(stops), "observed_block_type_counts": dict(blocks),
                   "passed": not report["failures"], "finished_utc": datetime.now(timezone.utc).isoformat()})
    require(len(json.dumps(report).encode()) < 128 * 1024 * 1024, "validation report exceeds 128 MiB")
    write_json(args.output, report)
    print(json.dumps({k: report[k] for k in ["passed", "prefix_complete_packets", "whole_frame_status_counts", "stop_counts", "failures"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
