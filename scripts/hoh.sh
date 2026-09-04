#!/usr/bin/env bash
set -euo pipefail

distribution="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# An installed package carries its own interpreter, so nothing on the machine has to
# provide one. A source checkout has no runtime/ directory and falls back to the
# system python3, which is what a developer wants.
embedded="$distribution/runtime/python/bin/python3"
if [[ -z "${HOH_PYTHON:-}" && -x "$embedded" ]]; then
  python_bin="$embedded"
else
  python_bin="${HOH_PYTHON:-python3}"
  if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "Python 3.11 or newer is required: $python_bin" >&2
    exit 1
  fi
  # Say which version is too old. Without this the run reached the first import of
  # a 3.11 name and died with an ImportError about datetime.UTC, which tells a
  # reader nothing about what to install.
  if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    found="$("$python_bin" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo unknown)"
    echo "Python 3.11 or newer is required; $python_bin is $found." >&2
    echo "Install a newer Python, or set HOH_PYTHON to one." >&2
    exit 1
  fi
fi
site_packages="$distribution/runtime/site-packages"
source_path="$distribution/src"
if [[ -d "$source_path/llm_harness" ]]; then
  package_path="$source_path"
elif [[ -d "$site_packages/llm_harness" ]]; then
  package_path="$site_packages"
else
  echo "HoH package is missing from $distribution" >&2
  exit 1
fi
# Tell the product where it is installed. Without it doctor resolves the
# embedded interpreter against the user's project and reports it missing.
export HOH_DISTRIBUTION_ROOT="$distribution"
if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$package_path:$PYTHONPATH"
else
  export PYTHONPATH="$package_path"
fi
exec "$python_bin" -m llm_harness "$@"
