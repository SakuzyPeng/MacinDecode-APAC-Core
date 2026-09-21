#!/usr/bin/env python3
"""Reproducible, bounded macOS acceptance checks. Standard library only.

afconvert has no end-frame option. One temporary full reference per representative
is therefore allowed up to --max-reference-mib, and deleted before the next one.
The CLI's own export quota remains unchanged.
"""
import argparse
import array
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def floats(data):
    result = array.array("f")
    result.frombytes(data)
    if sys.byteorder != "little":
        result.byteswap()
    require(all(math.isfinite(v) for v in result), "non-finite reference PCM")
    return result


def caf_chunks(path):
    result = {}
    with path.open("rb") as f:
        require(f.read(8) == b"caff\x00\x01\x00\x00", "invalid CAF header")
        while raw := f.read(12):
            require(len(raw) == 12, "truncated CAF chunk header")
            tag, size = struct.unpack(">4sq", raw)
            pos = f.tell()
            if size == -1 and tag == b"data":
                size = path.stat().st_size - pos
            require(0 <= size <= path.stat().st_size - pos, "invalid CAF chunk size")
            result[tag] = (pos, size)
            f.seek(size, 1)
    return result


def caf_payload(path, tag):
    pos, size = caf_chunks(path)[tag]
    with path.open("rb") as f:
        f.seek(pos)
        return f.read(size)


def caf_pcm(path, start, frames):
    rate, codec, flags, packet_bytes, packet_frames, channels, bits = struct.unpack(">d4sIIIII", caf_payload(path, b"desc"))
    require(codec == b"lpcm" and bits == 32 and flags & 3 == 3, "expected little-endian Float32 CAF")
    require(packet_frames == 1 and packet_bytes == channels * 4, "unexpected PCM framing")
    pos, size = caf_chunks(path)[b"data"]
    valid_frames = (size - 4) // (channels * 4)
    require(0 <= start <= valid_frames and start + frames <= valid_frames, "reference frame range out of bounds")
    with path.open("rb") as f:
        f.seek(pos + 4 + start * channels * 4)
        data = f.read(frames * channels * 4)
    require(len(data) == frames * channels * 4, "short reference PCM")
    floats(data)
    return data, valid_frames, channels, rate


def caf_packet_sizes(path):
    pakt = caf_payload(path, b"pakt")
    count, _valid, _prime, _remainder = struct.unpack(">qqii", pakt[:24])
    sizes = []
    cursor = 24
    for _ in range(count):
        value = 0
        for _ in range(10):
            byte = pakt[cursor]
            cursor += 1
            value = (value << 7) | (byte & 127)
            if not byte & 128:
                break
        else:
            raise AssertionError("invalid CAF packet-size varint")
        sizes.append(value)
    require(cursor == len(pakt), "unexpected CAF packet-table trailing data")
    return sizes


class Validator:
    def __init__(self, args):
        self.args = args
        self.binary = args.binary.resolve()
        self.report = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
                       "binary": str(self.binary), "corpus": str(args.corpus.resolve()),
                       "reference_backend": "/usr/bin/afconvert -f caff -d LEF32; full sequential reference, one file at a time",
                       "reference_limit_bytes": args.max_reference_mib * 1024**2,
                       "representatives": [], "fixtures": [], "failures": []}
        self.max_reference_bytes = 0

    def command(self, *args, expected=0):
        p = subprocess.run([str(self.binary), *map(str, args)], capture_output=True, text=True, timeout=120)
        require(p.returncode == expected, f"{args[0]} exited {p.returncode}, expected {expected}: {p.stderr[-4000:]}")
        return json.loads(p.stdout) if p.stdout.strip() else None

    def afconvert(self, source, output):
        info = self.command("inspect", source)
        frames = info["packet_table"]["value"]["valid_frames"]
        expected = frames * info["format"]["channels"] * 4 + 65536
        require(expected <= self.args.max_reference_mib * 1024**2, "reference exceeds configured temporary-file limit")
        require(shutil.disk_usage(output.parent).free > expected + 256 * 1024**2, "insufficient free space for temporary reference")
        p = subprocess.run(["/usr/bin/afconvert", str(source), str(output), "-f", "caff", "-d", "LEF32"], capture_output=True, text=True, timeout=120)
        require(p.returncode == 0, f"afconvert failed: {p.stderr}")
        require(output.stat().st_size <= self.args.max_reference_mib * 1024**2, "afconvert exceeded the expected reference size")
        self.max_reference_bytes = max(self.max_reference_bytes, output.stat().st_size)
        return info

    def compare_slice(self, info, native_dir, reference_caf, start, frames, destination):
        pcm = json.loads((native_dir / "pcm.json").read_text())
        data, total, channels, rate = caf_pcm(reference_caf, start, frames)
        require(channels == info["format"]["channels"] and rate == info["format"]["sample_rate"], "reference format mismatch")
        require(total == info["packet_table"]["value"]["valid_frames"], "reference valid-frame count mismatch")
        require(pcm["frames"] == frames and pcm["start_frame"] == start, "native range mismatch")
        destination.mkdir()
        (destination / "pcm.f32le").write_bytes(data)
        sidecar = copy.deepcopy(pcm)
        sidecar["sha256"] = sha(data)
        write_json(destination / "pcm.json", sidecar)
        comparison = self.command("compare", destination / "pcm.json", native_dir / "pcm.json")
        require(comparison["passed"], "PCM comparison did not pass")
        return {"start_frame": start, "frames": frames, "comparison": comparison,
                "native_sha256": pcm["sha256"], "afconvert_sha256": sidecar["sha256"]}

    def check_dump(self, source, destination, packets=150, start=0):
        manifest = self.command("dump", source, "--out", destination, "--start-packet", start, "--packets", packets)
        rows = [json.loads(line) for line in (destination / "packets.jsonl").read_text().splitlines()]
        data = (destination / "packets.bin").read_bytes()
        require(len(rows) == manifest["actual_packets"], "packet index count mismatch")
        require(sha(data) == manifest["packet_data_sha256"], "packet-data hash mismatch")
        offset = 0
        for i, row in enumerate(rows):
            require(row["packet_index"] == start + i and row["export_offset"] == offset, "packet offset/index mismatch")
            require(sha(data[offset:offset + row["bytes"]]) == row["sha256"], "per-packet hash mismatch")
            offset += row["bytes"]
        require(offset == len(data), "packet sizes do not cover export")
        cookie = (destination / "cookie.bin").read_bytes()
        require(sha(cookie) == manifest["file"]["cookie"]["value"]["sha256"], "cookie hash mismatch")
        source_verified = False
        if source.suffix.lower() == ".caf":
            require(cookie == caf_payload(source, b"kuki"), "cookie differs from raw CAF kuki")
            sizes = caf_packet_sizes(source)
            require([r["bytes"] for r in rows] == sizes[start:start + len(rows)], "packet sizes differ from raw CAF pakt")
            data_start, _ = caf_chunks(source)[b"data"]
            with source.open("rb") as f:
                f.seek(data_start + 4 + sum(sizes[:start]))
                require(f.read(len(data)) == data, "export differs from original CAF payload")
            source_verified = True
        return {"requested": packets, "actual": len(rows), "data_bytes": len(data), "cookie_matches_source_caf": source_verified,
                "independent_packet_indices": [r["packet_index"] for r in rows if r["dependency"]["value"] and r["dependency"]["value"]["independently_decodable"]]}

    def representative(self, source):
        info = self.command("inspect", source)
        afinfo = subprocess.check_output(["/usr/bin/afinfo", str(source)], text=True)
        match = re.search(r"Data format:\s*(\d+) ch,\s*([\d.]+) Hz,", afinfo)
        require(match is not None, "cannot parse afinfo format")
        require(int(match[1]) == info["format"]["channels"] and float(match[2]) == info["format"]["sample_rate"], "afinfo disagrees with native format")
        layout = re.search(r"Channel layout:\s*([^\n]+)", afinfo)[1].strip()
        require(layout == info["layout"]["value"]["name"], "afinfo disagrees with native channel layout")
        total = info["packet_table"]["value"]["valid_frames"]
        result = {"source": str(source), "file_bytes": source.stat().st_size, "channels": info["format"]["channels"],
                  "sample_rate": info["format"]["sample_rate"], "valid_frames": total, "layout": info["layout"]["value"], "ranges": []}
        with tempfile.TemporaryDirectory(prefix="apac-acceptance-") as tmp:
            root = Path(tmp)
            result["packets"] = self.check_dump(source, root / "packets")
            count = info["packet_count"]["value"]
            tail_packets = min(3, count)
            result["packet_tail"] = self.check_dump(source, root / "tail-packets", 64, count - tail_packets)
            require(result["packet_tail"]["actual"] == tail_packets, "tail packet read did not stop at EOF")
            reference = root / "reference.caf"
            self.afconvert(source, reference)
            refinfo = self.command("inspect", reference)
            require(refinfo["layout"]["value"] == info["layout"]["value"], "afconvert changed layout")
            for name, start in [("start", 0), ("middle", total // 2), ("end", max(0, total - 8192))]:
                frames = min(8192, total - start)
                print(f"  {name}: {source.name}", flush=True)
                native_dir = root / name
                self.command("decode", source, "--out", native_dir, "--start-frame", start, "--frames", frames)
                result["ranges"].append(self.compare_slice(info, native_dir, reference, start, frames, root / (name + "-afconvert")))
            tail_frames = min(17, total)
            partial = self.command("decode", source, "--out", root / "partial-tail", "--start-frame", total - tail_frames, "--frames", 8192)
            require(partial["frames"] == tail_frames, "partial PCM tail did not stop at EOF")
            result["partial_tail_frames"] = partial["frames"]
        result["passed"] = True
        return result

    def fixtures(self):
        root = self.args.fixtures.resolve()
        self.command("fixture", "--out", root)
        for name in ["silence", "impulse", "sine", "sweep", "noise", "channel-solo"]:
            print(f"fixture: stereo/{name}", flush=True)
            case = root / name
            source = case / "encoded.caf"
            info = self.command("inspect", source)
            frames = info["packet_table"]["value"]["valid_frames"]
            with tempfile.TemporaryDirectory(prefix="apac-fixture-check-") as tmp:
                t = Path(tmp)
                reference = t / "reference.caf"
                self.afconvert(source, reference)
                compared = self.compare_slice(info, case / "reference", reference, 0, frames, t / "afconvert")
            self.report["fixtures"].append({"layout": "stereo", "signal": name, "frames": frames, "comparison": compared["comparison"]})
        # Only small stereo vectors persist; multi-channel vectors are regenerated on demand.
        for preset in ["surround71", "surround714", "hoa3", "surround222"]:
            print(f"fixture: {preset}/channel-solo", flush=True)
            with tempfile.TemporaryDirectory(prefix="apac-layout-check-") as tmp:
                t = Path(tmp)
                self.command("fixture", "--out", t / "fixture", "--layout", preset, "--signals", "channel-solo")
                case = t / "fixture/channel-solo"
                source = case / "encoded.caf"
                info = self.command("inspect", source)
                frames = info["packet_table"]["value"]["valid_frames"]
                reference = t / "reference.caf"
                self.afconvert(source, reference)
                compared = self.compare_slice(info, case / "reference", reference, 0, frames, t / "afconvert")
                data = floats((case / "reference/pcm.f32le").read_bytes())
                channels = info["format"]["channels"]
                dominant = []
                for slot in range(channels):
                    begin = int((slot + 0.25) * frames / channels)
                    end = int((slot + 0.75) * frames / channels)
                    energy = [sum(float(data[f * channels + ch])**2 for f in range(begin, end)) for ch in range(channels)]
                    winner = max(range(channels), key=lambda ch: energy[ch])
                    dominant.append(winner)
                    require(winner == slot and energy[winner] > 0, f"channel-solo output maps channel {slot} to {winner}")
                self.report["fixtures"].append({"layout": preset, "signal": "channel-solo", "frames": frames,
                                                 "dominant_channel_per_slot": dominant, "comparison": compared["comparison"]})

    def run(self):
        manifest = self.args.report.parent / "corpus.jsonl"
        summary = self.command("scan", self.args.corpus, "--output", manifest)
        write_json(self.args.report.parent / "corpus-summary.json", summary)
        self.report["corpus_index"] = {k: summary[k] for k in ["files_discovered", "successful", "errors", "skipped_symlinks"]}
        rows = [json.loads(line)["file"] for line in manifest.read_text().splitlines()]
        groups = {}
        for row in rows:
            path = Path(row["source"])
            group = path.relative_to(self.args.corpus.resolve()).parts[0]
            if group not in groups or row["file_bytes"] < groups[group]["file_bytes"]:
                groups[group] = row
        for group, row in sorted(groups.items()):
            print(f"representative: {group}", flush=True)
            try:
                self.report["representatives"].append(self.representative(Path(row["source"])))
            except Exception as error:
                self.report["failures"].append({"group": group, "error": str(error)})
                print(f"FAILED: {group}: {error}", flush=True)
        try:
            self.fixtures()
        except Exception as error:
            self.report["failures"].append({"stage": "fixtures", "error": str(error)})
            print(f"FAILED: fixtures: {error}", flush=True)
        self.report["maximum_temporary_reference_bytes"] = self.max_reference_bytes
        self.report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        self.report["passed"] = not self.report["failures"]
        write_json(self.args.report, self.report)
        print(json.dumps({"passed": self.report["passed"], "representatives": len(self.report["representatives"]),
                          "fixture_checks": len(self.report["fixtures"]), "failures": self.report["failures"]}, ensure_ascii=False), flush=True)
        return 0 if self.report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("target/debug/apac-tool"))
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("reports/validation.json"))
    parser.add_argument("--fixtures", type=Path, default=Path("artifacts/fixtures/stereo"))
    parser.add_argument("--max-reference-mib", type=int, default=512)
    args = parser.parse_args()
    if args.max_reference_mib <= 0:
        parser.error("--max-reference-mib must be positive")
    for path in [args.report, args.report.parent / "corpus.jsonl", args.report.parent / "corpus-summary.json", args.fixtures]:
        if path.exists():
            parser.error(f"refusing to overwrite {path}; select a fresh report/fixture location")
    return Validator(args).run()


if __name__ == "__main__":
    sys.exit(main())
