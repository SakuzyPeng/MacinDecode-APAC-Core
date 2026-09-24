"""Portable executable discovery for CLI acceptance tests."""
import os
from pathlib import Path


def default_binary():
    override = os.environ.get('APAC_TOOL_BINARY')
    if override:
        return Path(override).resolve()
    name = 'apac-tool.exe' if os.name == 'nt' else 'apac-tool'
    return Path(__file__).resolve().parents[1] / 'target/debug' / name


def required_binary():
    binary = default_binary()
    if not binary.is_file():
        raise FileNotFoundError('build apac-tool or set APAC_TOOL_BINARY: ' + str(binary))
    return binary
