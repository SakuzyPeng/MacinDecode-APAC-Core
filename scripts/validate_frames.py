"""Portable validation utilities migrated from the research suite."""
from pathlib import Path
import hashlib
import itertools
import json
import subprocess
from validate import require
from spectrum_vectors import frame, matrix_cases

def control_specs():
    cases = []
    for signal in ["silence", "impulse", "sine", "sweep", "noise", "channel-solo"]:
        for mode in ["default", "none"]:
            cases.append((signal + "-" + mode, signal, [] if mode == "default" else ["--drc-configuration", "none"]))
    for name, extra in [("rate-44100", ["--sample-rate", "44100"]), ("quality-96", ["--quality", "96"]),
                        ("bitrate-256000", ["--bitrate", "256000"])]:
        cases.append((name, "sine", ["--drc-configuration", "none", *extra]))
    return cases
