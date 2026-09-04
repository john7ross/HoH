#!/usr/bin/env bash
set -euo pipefail

distribution="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python_bin="${HOH_PYTHON:-python3}"
target="$distribution/runtime/site-packages"
wheelhouse="$distribution/vendor/wheels"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python 3.11 or newer is required: $python_bin" >&2; exit 1
fi
mkdir -p "$target"
"$python_bin" -m pip install --no-index --find-links "$wheelhouse" --target "$target" --upgrade --no-deps llm-harness
echo "Offline package install ready: $target"
