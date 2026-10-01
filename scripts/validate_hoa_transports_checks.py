#!/usr/bin/env python3
"""Run new transport contracts and affected HOA/discrete regressions only."""
from validate_hoa_component_orders_checks import main
if __name__=='__main__':
    raise SystemExit(main(first_order=True,salient_counts=True,ambient_counts=True,quantization=True,expanded_orders=True,transports=True))
