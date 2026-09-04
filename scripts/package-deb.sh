#!/usr/bin/env bash
set -euo pipefail

project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
output="${1:-$project/dist}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "The Debian package must be built on Linux." >&2
  exit 1
fi
for command in python3 git dpkg-deb; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing build command: $command" >&2; exit 1; }
done
python3 - <<'PY'
import sys
import tkinter
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required")
print(f"Python {sys.version.split()[0]}, Tk {tkinter.TkVersion}")
PY

# A Windows checkout mounted through WSL can contain CRLF while its canonical Git
# content is clean. Normalize text exactly as Windows Git does; content changes and
# untracked files still remain visible and block the release.
if [[ -n "$(git -c core.autocrlf=true -C "$project" status --porcelain)" ]]; then
  echo "Refusing to build a release from a dirty Git checkout." >&2
  exit 1
fi
# The package carries its own interpreter, so a user needs nothing installed. Without
# it the package would silently go back to depending on the system python3, which is
# absent or too old on the machines this change exists to support.
if [[ ! -x "$project/runtime/python/bin/python3" ]]; then
  echo "The embedded runtime is missing. Run scripts/bootstrap-runtime.sh first." >&2
  exit 1
fi

version="$(sed -nE 's/^version[[:space:]]*=[[:space:]]*"([^"]+)"/\1/p' "$project/pyproject.toml" | tr -d '\r')"
[[ -n "$version" ]] || { echo "Cannot read the HoH version." >&2; exit 1; }
architecture="$(dpkg --print-architecture)"

export PYTHONPATH="$project/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m unittest discover -s "$project/tests"
python3 -m compileall -q "$project/src" "$project/tests"
doctor_dir="$(mktemp -d)"
doctor_config="$doctor_dir/doctor.toml"
stage="$(mktemp -d)"
cleanup() {
  rm -rf "$doctor_dir"
  rm -rf "$stage"
}
trap cleanup EXIT
printf '%s\n' \
  '[runtime]' 'require_embedded_python = false' 'wheels_path = "vendor/wheels"' \
  '[worker]' 'driver = "stub"' 'command = "stub"' 'args = []' \
  '[[agents]]' 'name = "release-smoke"' 'command = "true"' > "$doctor_config"
python3 -m llm_harness doctor --project-root "$project" --config "$doctor_config"

install -d "$stage/DEBIAN" "$stage/opt/hoh" "$stage/usr/bin" \
  "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps"
for item in \
  ARCHITECTURE.md ARCHITECTURE.ru.md README.md README.ru.md RELEASE_NOTES.md RELEASE_NOTES.ru.md \
  SECURITY.md SECURITY.ru.md THIRD-PARTY-NOTICES.md THIRD-PARTY-NOTICES.ru.md \
  CONTRIBUTING.md CONTRIBUTING.ru.md \
  catalogs config.example.toml docs examples LICENSE NOTICE \
  requirements.lock runtime schemas scripts src vendor; do
  if [[ -e "$project/$item" ]]; then
    cp -a "$project/$item" "$stage/opt/hoh/"
  fi
done
# A .pyc records the absolute path it was compiled from, so shipping the builder's
# __pycache__ prints the maintainer's home directory in every user-visible traceback.
# The suite this script just ran creates them, so they are always there. Python
# regenerates them on the user's machine on first import.
# The interpreter's own stdlib is excluded: that bytecode is produced upstream, names
# no path of ours, and deleting it would make every start recompile the standard
# library into a root-owned directory it cannot write.
runtime_stage="$stage/opt/hoh/runtime/python"
find "$stage/opt/hoh" -path "$runtime_stage" -prune -o \
  -type d -name '__pycache__' -prune -exec rm -rf {} +
leftovers="$(find "$stage/opt/hoh" -path "$runtime_stage" -prune -o \
  -type f \( -name '*.pyc' -o -name '*.pyo' \) -print | wc -l)"
if [[ "$leftovers" -ne 0 ]]; then
  echo "Refusing to ship compiled bytecode: $leftovers file(s) remain under $stage." >&2
  exit 1
fi
# The interpreter's stdlib bytecode is regenerated rather than shipped as found.
# The runtime lives inside the checkout, so anything that ran the embedded
# interpreter from here -- a doctor run, a launcher -- rewrote those .pyc with
# paths under the build directory, and they would print the maintainer's home in
# a user's traceback. Compiling with -d records the installed location instead,
# and doing it here keeps the guarantee the earlier purge makes for our own code
# without forcing every start to recompile the standard library.
stdlib_stage="$(find "$runtime_stage/lib" -maxdepth 1 -type d -name 'python3.*' | head -1)"
[[ -n "$stdlib_stage" ]] || { echo "Cannot find the staged standard library." >&2; exit 1; }
find "$stdlib_stage" -type d -name '__pycache__' -prune -exec rm -rf {} +
"$runtime_stage/bin/python3" -m compileall -q -j 0   -d "/opt/hoh/runtime/python/lib/$(basename "$stdlib_stage")" "$stdlib_stage" >/dev/null

# Materialize the list first. Under "set -o pipefail" a grep that matches nothing
# exits 1 and takes the whole script down silently, which is what a clean tree
# looks like -- the gate would fail exactly when it should pass.
named="$(grep -rl "$project" "$stage/opt/hoh" 2>/dev/null || true)"
if [[ -n "$named" ]]; then
  echo "Refusing to ship the build machine's path:" >&2
  printf '%s\n' "$named" | head -5 >&2
  exit 1
fi

# A checkout on a Windows mount reports 0777 for every entry. Ship deterministic modes instead,
# otherwise every installed module is world-writable and executed by whoever runs hoh.
find "$stage/opt/hoh" -type d -exec chmod 0755 {} +
find "$stage/opt/hoh" -type f -exec chmod 0644 {} +
# The environment template is sourced, not executed, and stays 0644.
find "$stage/opt/hoh/scripts" -type f -name '*.sh' ! -name '*.template.sh' -exec chmod 0755 {} +
# The sweep above just took the execute bit off the interpreter and every shared
# object it loads, which would leave the package installable and completely dead.
find "$runtime_stage/bin" -type f -exec chmod 0755 {} +
find "$runtime_stage" -type f -name '*.so' -exec chmod 0755 {} +
find "$runtime_stage" -type f -name '*.so.*' -exec chmod 0755 {} +
if ! "$runtime_stage/bin/python3" -c 'import tkinter' >/dev/null 2>&1; then
  echo "The staged interpreter cannot run, or has no tkinter." >&2
  exit 1
fi
printf '%s\n' \
  'Package: hoh' \
  "Version: $version" \
  'Section: devel' \
  'Priority: optional' \
  "Architecture: $architecture" \
  'Depends: git' \
  'Maintainer: Sergey Lebedev <john.spb.ross@gmail.com>' \
  'Description: Agent orchestration desktop application' \
  ' HoH coordinates Supervisor, Worker and Critic roles.' > "$stage/DEBIAN/control"
install -d "$stage/usr/share/doc/hoh"
{
  printf '%s\n\n' 'Upstream-Name: HoH'
  printf '%s\n\n' 'Files: *'
  printf '%s\n' 'Copyright: 2026 Sergey Lebedev'
  printf '%s\n\n' 'License: Apache-2.0'
  printf '%s\n' 'License: Apache-2.0'
  sed 's/^$/./; s/^/ /' "$project/LICENSE"
} > "$stage/usr/share/doc/hoh/copyright"
chmod 0644 "$stage/usr/share/doc/hoh/copyright"
printf '%s\n' \
  '#!/usr/bin/env sh' \
  'set -eu' \
  'updated="${XDG_DATA_HOME:-$HOME/.local/share}/hoh/hoh"' \
  'if [ -x "$updated" ]; then exec "$updated" "$@"; fi' \
  'exec /opt/hoh/scripts/hoh.sh "$@"' > "$stage/usr/bin/hoh"
printf '%s\n' \
  '#!/usr/bin/env sh' \
  'set -eu' \
  'updated="${XDG_DATA_HOME:-$HOME/.local/share}/hoh/hoh-gui"' \
  'if [ -x "$updated" ]; then exec "$updated" "$@"; fi' \
  'exec /opt/hoh/scripts/hoh-gui.sh "$@"' > "$stage/usr/bin/hoh-gui"
chmod 0755 "$stage/usr/bin/hoh" "$stage/usr/bin/hoh-gui"
install -m 0644 "$project/packaging/linux/hoh.desktop" "$stage/usr/share/applications/hoh.desktop"
install -m 0644 "$project/packaging/linux/hoh.svg" "$stage/usr/share/icons/hicolor/scalable/apps/hoh.svg"

mkdir -p "$output"
package="$output/hoh_${version}_${architecture}.deb"
rm -f "$package"
dpkg-deb --build --root-owner-group "$stage" "$package"

contents="$(dpkg-deb -c "$package")"
world_writable="$(printf '%s\n' "$contents" | awk '$1 ~ /^[-d].{8}w/ {print $NF}')"
if [[ -n "$world_writable" ]]; then
  echo "Refusing to ship world-writable paths:" >&2
  printf '%s\n' "$world_writable" >&2
  exit 1
fi
# Materialize the path list first: piping into `grep -q` under `pipefail` fails the
# pipeline when grep exits early and awk gets SIGPIPE, even on a match.
packaged_paths="$(printf '%s\n' "$contents" | awk '{print $NF}')"
# The package embeds CPython, Tcl/Tk, OpenSSL and others, each under its own
# licence. The file that names them has to travel with the code it describes.
for required in ./opt/hoh/LICENSE ./opt/hoh/NOTICE ./opt/hoh/README.ru.md \
  ./opt/hoh/THIRD-PARTY-NOTICES.md ./opt/hoh/THIRD-PARTY-NOTICES.ru.md \
  ./usr/share/doc/hoh/copyright; do
  if ! grep -qxF -- "$required" <<<"$packaged_paths"; then
    echo "Package is missing required file: $required" >&2
    exit 1
  fi
done

printf '%s  %s\n' "$(sha256sum "$package" | awk '{print $1}')" "$(basename "$package")" > "$package.sha256"
echo "Debian package ready: $package"
