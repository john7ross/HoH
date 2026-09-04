#!/usr/bin/env bash
# Place the interpreter that ships inside the Linux package under runtime/python.
#
# The Debian package carries its own Python for the same reason the Windows installer
# does: a user should not have to install anything to run HoH. Relying on the system
# python3 excluded three real cases -- a machine with no network (apt cannot fetch
# python3-tk, which is priority "optional" and absent by default), a distribution
# older than Python 3.11 such as Ubuntu 22.04, and any minimal image or container.
#
# The build is taken from python-build-standalone rather than compiled here, because
# its binaries are relocatable and link against glibc 2.17. An interpreter built on
# this machine would demand the glibc of this machine: on Ubuntu 26.04 that is 2.43,
# which no released Ubuntu LTS provides, so the "self-contained" package would run
# almost nowhere. The release and its checksum are pinned; a build either gets exactly
# this interpreter or fails.
set -euo pipefail

project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
destination="${1:-$project/runtime/python}"

RELEASE="20260901"
PYTHON_VERSION="3.11.16"

case "$(uname -s)" in
  Linux) ;;
  *) echo "The embedded Linux runtime can only be prepared on Linux." >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64)  target="x86_64-unknown-linux-gnu";  sha256="64427febea27864d136db46c8efe968eb6fa5ca2813ce1dca4bb95aec31cb2e4" ;;
  aarch64) target="aarch64-unknown-linux-gnu"; sha256="f9308ed57184fae73941c712ed847be420ce36d31ba73ce53f0508a1ee752f68" ;;
  *)
    echo "No pinned interpreter for $(uname -m). Add its checksum from the ${RELEASE} release." >&2
    exit 1
    ;;
esac

asset="cpython-${PYTHON_VERSION}+${RELEASE}-${target}-install_only_stripped.tar.gz"
url="https://github.com/astral-sh/python-build-standalone/releases/download/${RELEASE}/${asset}"

work="$(mktemp -d)"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

echo "Fetching $asset"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL --retry 3 -o "$work/$asset" "$url"
elif command -v wget >/dev/null 2>&1; then
  wget -q -O "$work/$asset" "$url"
else
  python3 - "$url" "$work/$asset" <<'PY'
import shutil, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=600) as response, open(sys.argv[2], "wb") as handle:
    shutil.copyfileobj(response, handle)
PY
fi

actual="$(sha256sum "$work/$asset" | awk '{print $1}')"
if [[ "$actual" != "$sha256" ]]; then
  echo "Checksum mismatch for $asset." >&2
  echo "  expected $sha256" >&2
  echo "  actual   $actual" >&2
  exit 1
fi
echo "Checksum verified."

tar -xzf "$work/$asset" -C "$work"
[[ -x "$work/python/bin/python3" ]] || { echo "The archive did not contain bin/python3." >&2; exit 1; }

# Nothing here is used at run time: C headers, IDLE, the 2to3 library and the terminfo
# database together add about ten megabytes to every download for no benefit.
rm -rf "$work/python/include" \
       "$work/python/share/terminfo" \
       "$work/python/lib/python${PYTHON_VERSION%.*}/idlelib" \
       "$work/python/lib/python${PYTHON_VERSION%.*}/lib2to3"

probe="$("$work/python/bin/python3" -c 'import sys, tkinter, ssl, sqlite3; print(sys.version.split()[0], tkinter.TkVersion)')"
echo "Interpreter reports: $probe"

rm -rf "$destination"
mkdir -p "$(dirname "$destination")"
mv "$work/python" "$destination"
echo "Embedded runtime ready: $destination"
