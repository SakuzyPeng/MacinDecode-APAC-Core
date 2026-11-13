"""Portable validation utilities migrated from the research suite."""
from pathlib import Path
import hashlib
import itertools
import json
import subprocess
from validate import require
from spectrum_vectors import frame, matrix_cases

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

def command(binary, *args, allowed=(0,)):
    result = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    require(result.returncode in allowed, f"{args[0]} returned {result.returncode}: {result.stderr[-4000:]}")
    return json.loads(result.stdout or result.stderr)
