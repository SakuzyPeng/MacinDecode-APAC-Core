"""Small shared helpers for portable validators."""
import json
from pathlib import Path

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
