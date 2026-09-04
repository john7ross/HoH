# Third-party notices

**English** · [Русский](THIRD-PARTY-NOTICES.ru.md)

HoH itself is licensed under Apache-2.0 (see [LICENSE](LICENSE) and
[NOTICE](NOTICE)). Its own source has no third-party runtime dependencies: the
program is written against the Python standard library alone, and
[`requirements.lock`](requirements.lock) is deliberately empty.

The **installable packages** are a different matter. Each one carries a complete
Python interpreter so that a user does not have to install anything, and that
interpreter brings its own third-party components, each keeping its **own**
licence. This file lists them and says where each licence text ships.

If you run HoH from a source checkout, none of this applies — you are using the
Python already on your machine, under whatever terms you obtained it.

## Which package contains what

| Component | `HoH-Setup-*.exe` | `hoh_*.deb` | `HoH-*.dmg` |
|---|---|---|---|
| HoH source (this project, Apache-2.0) | yes | yes | yes |
| CPython | 3.11.15 | 3.11.16 | 3.12.10 |
| Tcl/Tk | 8.6 | 9.0 | 8.6 |
| OpenSSL | 3.5.7 | 3.5.8 | 3.x |
| SQLite | 3.53.1 | 3.53.1 | yes |
| zlib | 1.3.1 | 1.3.2 | yes |
| libffi, liblzma, bzip2, Expat, mpdecimal | yes | yes | yes |
| PyInstaller bootloader | no | no | yes |

The Windows and Linux packages take their interpreter from the same upstream,
[python-build-standalone](https://github.com/astral-sh/python-build-standalone),
pinned by release and SHA-256 in `scripts/bootstrap-runtime.ps1` and
`scripts/bootstrap-runtime.sh`. The macOS application is frozen by PyInstaller
from a python.org framework build.

## CPython — **PSF License Agreement**

The interpreter, its standard library and the extension modules built with it.

- Installed at `runtime/python/` (Windows, Linux) and inside
  `HoH.app/Contents/Frameworks/Python.framework` (macOS).
- Licence text ships with the interpreter: `runtime/python/lib/python3.11/LICENSE.txt`
  on Linux, `runtime/python/LICENSE.txt` on Windows.
- Upstream: <https://www.python.org/> · <https://docs.python.org/3/license.html>

The PSF License Agreement is a permissive BSD-style licence. It requires the
copyright notice to be retained, which shipping the interpreter's own
`LICENSE.txt` satisfies.

## Tcl/Tk — **BSD-style (Tcl/Tk licence)**

The GUI toolkit behind `tkinter`, which is the entire HoH desktop interface.

- Windows: `runtime/python/DLLs/tcl86t.dll`, `tk86t.dll` and `runtime/python/tcl/`.
- Linux: `runtime/python/lib/libtcl9.0.so`, `libtcl9tk9.0.so` and the
  `runtime/python/lib/tcl9*`, `tk9.0` and `itcl*` directories.
- macOS: `HoH.app/Contents/Frameworks/libtcl8.6.dylib`, `libtk8.6.dylib`,
  `_tcl_data` and `_tk_data`.
- Upstream: <https://www.tcl-lang.org/software/tcltk/license.html>

The Tcl/Tk licence is permissive and requires the copyright notice to be kept
with the distribution.

## OpenSSL — **Apache-2.0**

Used by the standard library's `ssl` and `hashlib` modules. HoH itself makes
HTTPS requests only when an operator points it at a provider endpoint.

- Windows: `runtime/python/DLLs/libssl-3-x64.dll`, `libcrypto-3-x64.dll`.
- Linux: linked into the interpreter's `_ssl` and `_hashlib` extension modules.
- macOS: `HoH.app/Contents/Frameworks/libssl.3.dylib`, `libcrypto.3.dylib`.
- Upstream: <https://www.openssl.org/source/license.html>

OpenSSL 3.x is Apache-2.0, the same licence as HoH, so
[`LICENSE`](LICENSE) is also the applicable licence text for this component.

## SQLite — **public domain**

Used by the standard library's `sqlite3` module.

- Windows: `runtime/python/DLLs/sqlite3.dll`. Linux and macOS: linked into the
  interpreter's `_sqlite3` extension module.
- Upstream: <https://www.sqlite.org/copyright.html>

SQLite is dedicated to the public domain and imposes no notice obligation. It is
listed here for completeness.

## zlib, bzip2, XZ Utils (liblzma) — **permissive**

Compression backends for `zipfile`, `gzip`, `bz2` and `lzma`. HoH uses them when
it reads and writes update payloads.

- zlib and bzip2 use their own permissive licences; XZ Utils' liblzma is in the
  public domain.
- Upstream: <https://zlib.net/zlib_license.html> ·
  <https://sourceware.org/bzip2/> · <https://tukaani.org/xz/>

## libffi — **MIT**

Backs the standard library's `ctypes`, which HoH uses on Windows to make the
interface DPI-aware.

- Upstream: <https://github.com/libffi/libffi/blob/master/LICENSE>

## Expat — **MIT**

Backs the standard library's XML parsers.

- Upstream: <https://github.com/libexpat/libexpat/blob/master/expat/COPYING>

## mpdecimal — **BSD-2-Clause**

Backs the standard library's `decimal` module.

- Upstream: <https://www.bytereef.org/mpdecimal/>

## PyInstaller bootloader — **GPL-2.0-or-later with a bundling exception**

macOS only. `HoH.app/Contents/MacOS/HoH` is a PyInstaller-frozen executable, and
PyInstaller's bootloader is compiled into it.

- Upstream: <https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt>

PyInstaller's licence carries an explicit exception permitting the bootloader to
be linked into a frozen application released under **any** terms, including
proprietary ones. That exception is why bundling it does not make HoH GPL. Only
the bootloader is PyInstaller code; PyInstaller is not otherwise part of the
shipped application, and it is not a dependency of HoH's own source.

## What is deliberately not here

- **Agents.** HoH launches Codex, Claude Code, Hermes, aider and the rest as
  separate processes over documented protocols. None of them ship inside any
  HoH package, and HoH neither links against them nor redistributes them. A user
  installs the agents they want under those projects' own terms.
- **Models.** No model weights are bundled. HoH talks to endpoints an operator
  configures.
- **Build tools.** Inno Setup, PlantUML and PyInstaller are used to produce and
  document the release. Except for the PyInstaller bootloader described above,
  none of their code reaches a user.

## Reporting a problem with this list

If a component is missing, misattributed or wrongly licensed here, please open an
issue at <https://github.com/john7ross/HoH/issues>. Licensing mistakes are
treated as defects, not as paperwork: they are fixed and the release is rebuilt.
