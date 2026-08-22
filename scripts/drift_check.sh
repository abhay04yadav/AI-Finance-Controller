#!/usr/bin/env bash
# Drift check — Review Guide part 3. Thin wrapper so `make drift-check` and
# `bash scripts/drift_check.sh` both work. The real checks live in drift_check.py,
# which reads code only (strings and comments are skipped) so the rules written
# in docstrings do not register as violations of themselves.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
exec "${PYTHON:-python}" scripts/drift_check.py "$@"
