#!/usr/bin/env python3
"""Factor/check all twelve dictionaries while preserving their expanded table digests."""
import argparse
from hoa_salient_format import DATA, format_name, shared_name, load_format, split_format, expand_format, json_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    outputs = {}
    for order in range(1, 4):
        for precision in range(6, 10):
            expanded = load_format(DATA / format_name(order, precision))
            stored, shared = split_format(expanded)
            if expand_format(stored, shared) != expanded:
                raise ValueError(f'packed dictionary does not round-trip at order {order}, q{precision}')
            name = shared_name(order)
            if name in outputs and outputs[name] != json_bytes(shared):
                raise ValueError(f'precision-dependent shared tables at order {order}')
            outputs[name] = json_bytes(shared)
            outputs[format_name(order, precision)] = json_bytes(stored)
    # All identities and shared payloads are checked before writing anything.
    for name, raw in outputs.items():
        path = DATA / name
        if args.check:
            if path.read_bytes() != raw:
                raise ValueError(f'noncanonical shared dictionary: {name}')
        else:
            path.write_bytes(raw)
    print(f'12 dictionaries, 3 shared payloads, {sum(map(len, outputs.values()))} bytes')


if __name__ == '__main__':
    main()
