#!/usr/bin/env python3
"""Bounded packet-replay acceptance: short windows only, no full-song reference output."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from validate import caf_pcm, require, write_json


def command(binary, *args, allowed=(0,)):
    result = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, timeout=120)
    require(result.returncode in allowed, f"{args[0]} returned {result.returncode}: {result.stderr[-4000:]}")
    return json.loads(result.stdout or result.stderr)


def check_accounting(report, pcm):
    require(report["complete"] and pcm["complete"] and pcm["all_finite"], "incomplete or non-finite replay")
    require(report["original_source_accessed"] is False, "replay used the original source")
    require(pcm["frames"] == report["saved_frames"] == report["range"]["frames"], "saved frame count mismatch")
    require(pcm["start_frame"] == report["range"]["start_frame"], "PCM origin mismatch")
    require(report["produced_raw_frames"] == report["saved_frames"] + report["discarded_before_frames"]
            + report["discarded_after_frames"], "raw frame accounting mismatch")
    require(report["consumed_packet_frames"] >= report["produced_raw_frames"], "more PCM produced than supplied packet frames")
    if report["range"]["drain_to_eof"]:
        require(report["eof_sent"] and report["eof_drained"], "EOF was not drained")
        require(report["consumed_packets"] == report["stored_packets"], "EOF before all packets")
        require(report["produced_raw_frames"] == report["stored_raw_end"] - report["stored_raw_start"], "raw EOF duration mismatch")


def run_batches(binary, bundle, reference, root, start, frames, case, af_reference=None):
    case["batches"] = []
    first = None
    ref = json.loads(reference.read_text())
    for batch in [1, 7, 64]:
        record = {"input_batch_packets": batch, "passed": False}
        case["batches"].append(record)
        try:
            out = root / f"replay-{batch}"
            replay = command(binary, "replay", bundle, "--out", out, "--start-frame", start,
                             "--frames", frames, "--input-batch-packets", batch)
            pcm = json.loads((out / "pcm.json").read_text())
            record["replay"] = replay
            record["pcm_sha256"] = pcm["sha256"]
            check_accounting(replay, pcm)
            require(pcm["frames"] == ref["frames"] and pcm["start_frame"] == ref["start_frame"], "reference range mismatch")
            for key in ["mdrc", "^pro", "ptlc", "pptl"]:
                a, b = pcm["decoder_settings"][key], ref["decoder_settings"][key]
                require(a["error"] is None and b["error"] is None and a["value"] == b["value"],
                        f"decoder setting {key} was not verified equal")
            comparison = command(binary, "compare", reference, out / "pcm.json", allowed=(0, 2))
            record["comparison"] = comparison
            require(comparison["passed"] and comparison["layout_verified"], "file-reference comparison failed")
            if first is not None:
                record["batch_comparison"] = command(binary, "compare", first, out / "pcm.json", allowed=(0, 2))
                require(record["batch_comparison"]["passed"], "input batch changed the result beyond tolerance")
            else:
                first = out / "pcm.json"
            if af_reference is not None:
                record["afconvert_comparison"] = command(binary, "compare", af_reference, out / "pcm.json", allowed=(0, 2))
                require(record["afconvert_comparison"]["passed"], "afconvert comparison failed")
            record["passed"] = True
        except Exception as error:
            record["error"] = str(error)
    require(len(case["batches"]) == 3 and all(b["passed"] for b in case["batches"]), "one or more input batches failed")


def representative(binary, source, expected_channels, records):
    info = command(binary, "inspect", source)
    require(info["format"]["channels"] == expected_channels, "representative channel count changed")
    table = info["packet_table"]["value"]
    require(table is not None, "packet table unavailable")
    valid, priming = table["valid_frames"], table["priming_frames"]
    fpp = info["format"]["frames_per_packet"]
    require(fpp > 0 and valid > 8192, "representative must have fixed packet duration and enough audio")
    for name, start in [("start", 0), ("middle", valid // 2), ("tail", valid - 8192)]:
        record = {"source": str(source), "channels": expected_channels, "range": name, "start_frame": start, "passed": False}
        records.append(record)
        print(f"replay representative: {expected_channels} channels / {name}", file=sys.stderr, flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix="apac-replay-reference-") as tmp:
                root = Path(tmp)
                first = 0 if start == 0 else (priming + start) // fpp
                end = (priming + start + 8192 + fpp - 1) // fpp
                bundle = root / "packets"
                record["dump"] = command(binary, "dump", source, "--out", bundle, "--start-packet", first,
                                         "--packets", end - first, "--with-preroll")
                ref = root / "reference"
                command(binary, "decode", source, "--out", ref, "--start-frame", start, "--frames", 8192)
                run_batches(binary, bundle, ref / "pcm.json", root, start, 8192, record)
                record["passed"] = True
        except Exception as error:
            record["error"] = str(error)


def short_vector(binary, signal, frames, records):
    record = {"signal": signal, "source_frames": frames, "passed": False}
    records.append(record)
    print(f"replay fixture: {signal} / {frames} frames", file=sys.stderr, flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix="apac-replay-fixture-") as tmp:
            root = Path(tmp)
            command(binary, "fixture", "--out", root / "fixture", "--signals", signal,
                    "--duration", frames / 48000, "--max-output-mib", 4)
            case = root / "fixture" / signal
            source = case / "encoded.caf"
            info = command(binary, "inspect", source)
            record["source_cookie"] = info["cookie"]
            record["packet_table"] = info["packet_table"]
            bundle = root / "packets"
            record["dump"] = command(binary, "dump", source, "--out", bundle, "--packets",
                                     info["packet_count"]["value"] + 3, "--with-preroll")
            caf = root / "afconvert.caf"
            result = subprocess.run(["/usr/bin/afconvert", str(source), str(caf), "-f", "caff", "-d", "LEF32"],
                                    capture_output=True, text=True, timeout=120)
            require(result.returncode == 0, f"afconvert failed: {result.stderr}")
            data, actual, channels, rate = caf_pcm(caf, 0, frames)
            require(actual == frames and channels == 2 and rate == 48000, "afconvert range/format mismatch")
            af = root / "af-reference"
            af.mkdir()
            metadata = copy.deepcopy(json.loads((case / "reference/pcm.json").read_text()))
            metadata["sha256"] = hashlib.sha256(data).hexdigest()
            metadata["decoder_settings"] = {}  # afconvert's processing properties were not queried.
            (af / "pcm.f32le").write_bytes(data)
            write_json(af / "pcm.json", metadata)
            source.unlink()  # Only this generated file is removed; real media are never modified.
            record["original_removed_before_replay"] = not source.exists()
            run_batches(binary, bundle, case / "reference/pcm.json", root, 0, frames + 1024, record, af / "pcm.json")
            for batch in record["batches"]:
                replay = batch["replay"]
                require(replay["eof_drained"] and replay["range"]["clipped_by"] == "source_eof", "short vector did not drain/clip EOF")
                require(replay["discarded_before_frames"] == info["packet_table"]["value"]["priming_frames"], "priming trim mismatch")
                require(replay["discarded_after_frames"] == info["packet_table"]["value"]["remainder_frames"], "padding trim mismatch")
            record["passed"] = True
    except Exception as error:
        record["error"] = str(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--representatives", type=Path, default=Path("reports/validation.json"))
    parser.add_argument("--binary", type=Path, default=Path("target/debug/apac-tool"))
    parser.add_argument("--output", type=Path, default=Path("reports/replay-validation.json"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("native replay acceptance requires macOS")
    if args.output.exists():
        parser.error("refusing to overwrite the validation report")
    baseline = json.loads(args.representatives.read_text())
    reps = baseline["representatives"]
    require(len(reps) == 5 and {r["channels"] for r in reps} == {2, 8, 12, 16, 24}, "requires the five representative groups")
    binary = args.binary.resolve()
    component = Path("/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs")
    with component.open("rb") as f:
        component_sha = hashlib.file_digest(f, "sha256").hexdigest()
    report = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "tested_worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
              "system": subprocess.check_output(["sw_vers"], text=True).strip(), "architecture": platform.machine(),
              "component_sha256": component_sha, "representative_baseline_sha256": hashlib.sha256(args.representatives.read_bytes()).hexdigest(),
              "atol": 1e-6, "rtol": 1e-5, "input_batches": [1, 7, 64],
              "representatives": [], "short_vectors": [], "failures": []}
    for source in reps:
        try:
            representative(binary, Path(source["source"]), source["channels"], report["representatives"])
        except Exception as error:
            report["failures"].append({"source": source["source"], "stage": "representative setup", "error": str(error)})
    for signal in ["silence", "impulse", "sine", "sweep", "noise", "channel-solo"]:
        for frames in [1, 17, 1023, 1024, 1025, 8192, 12000]:
            short_vector(binary, signal, frames, report["short_vectors"])
    for kind in ["representatives", "short_vectors"]:
        for i, result in enumerate(report[kind]):
            if not result["passed"]:
                report["failures"].append({"stage": kind, "index": i, "error": result["error"]})
    if len(report["representatives"]) != 15 or len(report["short_vectors"]) != 42:
        report["failures"].append({"stage": "coverage", "error": "expected 15 representative windows and 42 short vectors"})
    report["passed"] = not report["failures"]
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(args.output, report)
    print(json.dumps({"passed": report["passed"], "representative_ranges": len(report["representatives"]),
                      "short_vectors": len(report["short_vectors"]), "failures": report["failures"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
