#!/usr/bin/env bash
# Layering check — guide section 3.2. Thin wrapper; the single implementation
# lives in check_layering.py so make, CI, and drift_check all agree.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
exec "${PYTHON:-python}" scripts/check_layering.py "$@"
