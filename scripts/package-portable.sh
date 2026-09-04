#!/usr/bin/env bash
set -euo pipefail

distribution="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
output="${1:-$distribution/dist}"
exec "$distribution/scripts/hoh.sh" package-portable --project-root "$distribution" --output-dir "$output"
