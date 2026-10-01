#!/usr/bin/env python3
"""Verify six ambient-count native controls against the bounded SCE decoder."""
import hoa_ambient_counts_vectors as vectors
from validate_hoa_salient_subbands_native import main
if __name__=='__main__':raise SystemExit(main(vectors=vectors))
