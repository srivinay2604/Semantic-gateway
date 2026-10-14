#!/usr/bin/env bash
set -euo pipefail

echo "=== CacheGate Load Testing Suite ==="
python benchmarks/load_test.py
