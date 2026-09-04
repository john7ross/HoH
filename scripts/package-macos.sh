#!/usr/bin/env bash
set -euo pipefail

project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
output="${1:-$project/dist}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS application must be built and tested on macOS." >&2
  exit 1
fi
for command in python3 git hdiutil codesign; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing build command: $command" >&2; exit 1; }
done
python3 -m PyInstaller --version >/dev/null 2>&1 || {
  echo "PyInstaller is required only on the release builder: python3 -m pip install PyInstaller==6.15.0" >&2
  exit 1
}
python3 - <<'PY'
import sys
import tkinter
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
print(f"Python {sys.version.split()[0]}, Tk {tkinter.TkVersion}")
PY
if [[ -n "$(git -C "$project" status --porcelain)" ]]; then
  echo "Refusing to build a release from a dirty Git checkout." >&2
  exit 1
fi

version="$(sed -nE 's/^version[[:space:]]*=[[:space:]]*"([^"]+)"/\1/p' "$project/pyproject.toml" | tr -d '\r')"
[[ -n "$version" ]] || { echo "Cannot read the HoH version." >&2; exit 1; }
export PYTHONPATH="$project/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m unittest discover -s "$project/tests"
python3 -m compileall -q "$project/src" "$project/tests"

work="$(mktemp -d)"
doctor_config="$work/doctor.toml"
cleanup() {
  rm -rf "$work"
}
trap cleanup EXIT
printf '%s\n' \
  '[runtime]' 'require_embedded_python = false' 'wheels_path = "vendor/wheels"' \
  '[worker]' 'driver = "stub"' 'command = "stub"' 'args = []' \
  '[[agents]]' 'name = "release-smoke"' 'command = "true"' > "$doctor_config"
python3 -m llm_harness doctor --project-root "$project" --config "$doctor_config"

python3 -m PyInstaller --noconfirm --clean --windowed --name HoH \
  --paths "$project/src" --distpath "$work/dist" --workpath "$work/build" \
  --specpath "$work" "$project/packaging/macos/hoh_gui.py"
app="$work/dist/HoH.app"
resources="$app/Contents/Resources/hoh"
mkdir -p "$resources"
for item in \
  ARCHITECTURE.md ARCHITECTURE.ru.md README.md README.ru.md RELEASE_NOTES.md RELEASE_NOTES.ru.md \
  SECURITY.md SECURITY.ru.md THIRD-PARTY-NOTICES.md THIRD-PARTY-NOTICES.ru.md \
  CONTRIBUTING.md CONTRIBUTING.ru.md \
  catalogs config.example.toml docs examples LICENSE NOTICE \
  requirements.lock schemas scripts vendor; do
  if [[ -e "$project/$item" ]]; then cp -a "$project/$item" "$resources/"; fi
done
# The environment template is sourced, not executed, and stays 0644.
find "$resources/scripts" -type f -name '*.sh' ! -name '*.template.sh' -exec chmod 0755 {} +
printf '%s\n' \
  '#!/usr/bin/env sh' \
  'set -eu' \
  '# .../HoH.app/Contents/Resources/hoh/scripts -> .../HoH.app/Contents' \
  'contents=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd -P)' \
  'exec "$contents/MacOS/HoH" "$@"' > "$resources/scripts/hoh.sh"
chmod 0755 "$resources/scripts/hoh.sh"
# Run the launcher that was just written. It is the documented way into the CLI
# from inside the bundle, and a wrong number of ".." here is invisible until a
# user tries it.
"$resources/scripts/hoh.sh" doctor --project-root "$work" --config "$doctor_config" >/dev/null
# The bundle embeds CPython, Tcl/Tk, OpenSSL and the PyInstaller bootloader, each
# under its own licence. The file that names them has to travel with them.
for required in LICENSE NOTICE THIRD-PARTY-NOTICES.md THIRD-PARTY-NOTICES.ru.md; do
  if [[ ! -f "$resources/$required" ]]; then
    echo "Application bundle is missing required file: $required" >&2
    exit 1
  fi
done

codesign --force --deep --sign - "$app"

dmg_root="$work/dmg"
mkdir -p "$dmg_root"
cp -a "$app" "$dmg_root/HoH.app"
ln -s /Applications "$dmg_root/Applications"
mkdir -p "$output"
dmg="$output/HoH-$version.dmg"
rm -f "$dmg"
hdiutil create -volname "HoH $version" -srcfolder "$dmg_root" -ov -format UDZO "$dmg"
printf '%s  %s\n' "$(shasum -a 256 "$dmg" | awk '{print $1}')" "$(basename "$dmg")" > "$dmg.sha256"
echo "macOS application ready: $dmg"
