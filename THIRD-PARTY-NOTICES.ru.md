# Уведомления о стороннем коде

[English](THIRD-PARTY-NOTICES.md) · **Русский**

Сам HoH распространяется по Apache-2.0 (см. [LICENSE](LICENSE) и
[NOTICE](NOTICE)). У его собственного исходного кода нет сторонних зависимостей
времени выполнения: программа написана на одной стандартной библиотеке Python, а
файл [`requirements.lock`](requirements.lock) намеренно пуст.

С **устанавливаемыми пакетами** иначе. Каждый из них несёт внутри полный
интерпретатор Python, чтобы пользователю не пришлось ничего доустанавливать, а
интерпретатор тянет за собой сторонние компоненты, и каждый сохраняет **свою**
лицензию. Этот файл перечисляет их и указывает, где лежит текст каждой лицензии.

Если вы запускаете HoH из исходников, ничего из перечисленного к вам не
относится: вы пользуетесь тем Python, который уже стоит на машине, на тех
условиях, на которых его получили.

## Что в каком пакете

| Компонент | `HoH-Setup-*.exe` | `hoh_*.deb` | `HoH-*.dmg` |
|---|---|---|---|
| Исходники HoH (этот проект, Apache-2.0) | да | да | да |
| CPython | 3.11.15 | 3.11.16 | 3.12.10 |
| Tcl/Tk | 8.6 | 9.0 | 8.6 |
| OpenSSL | 3.5.7 | 3.5.8 | 3.x |
| SQLite | 3.53.1 | 3.53.1 | да |
| zlib | 1.3.1 | 1.3.2 | да |
| libffi, liblzma, bzip2, Expat, mpdecimal | да | да | да |
| Загрузчик PyInstaller | нет | нет | да |

Пакеты для Windows и Linux берут интерпретатор из одного источника —
[python-build-standalone](https://github.com/astral-sh/python-build-standalone);
релиз и SHA-256 закреплены в `scripts/bootstrap-runtime.ps1` и
`scripts/bootstrap-runtime.sh`. Приложение для macOS замораживает PyInstaller из
сборки python.org.

## CPython — **PSF License Agreement**

Интерпретатор, его стандартная библиотека и собранные вместе с ним модули
расширения.

- Устанавливается в `runtime/python/` (Windows, Linux) и внутрь
  `HoH.app/Contents/Frameworks/Python.framework` (macOS).
- Текст лицензии поставляется вместе с интерпретатором:
  `runtime/python/lib/python3.11/LICENSE.txt` на Linux,
  `runtime/python/LICENSE.txt` на Windows.
- Первоисточник: <https://www.python.org/> ·
  <https://docs.python.org/3/license.html>

PSF License Agreement — разрешительная лицензия в духе BSD. Она требует
сохранять уведомление об авторских правах, и поставка собственного файла
`LICENSE.txt` интерпретатора это требование закрывает.

## Tcl/Tk — **BSD-подобная (лицензия Tcl/Tk)**

Библиотека графического интерфейса, на которой работает `tkinter`, — то есть весь
десктопный интерфейс HoH.

- Windows: `runtime/python/DLLs/tcl86t.dll`, `tk86t.dll` и `runtime/python/tcl/`.
- Linux: `runtime/python/lib/libtcl9.0.so`, `libtcl9tk9.0.so` и каталоги
  `runtime/python/lib/tcl9*`, `tk9.0`, `itcl*`.
- macOS: `HoH.app/Contents/Frameworks/libtcl8.6.dylib`, `libtk8.6.dylib`,
  `_tcl_data` и `_tk_data`.
- Первоисточник: <https://www.tcl-lang.org/software/tcltk/license.html>

Лицензия Tcl/Tk разрешительная и требует сохранять уведомление об авторских
правах вместе с дистрибутивом.

## OpenSSL — **Apache-2.0**

Используется модулями стандартной библиотеки `ssl` и `hashlib`. Сам HoH делает
HTTPS-запросы только тогда, когда оператор указал ему адрес провайдера.

- Windows: `runtime/python/DLLs/libssl-3-x64.dll`, `libcrypto-3-x64.dll`.
- Linux: вкомпилирован в модули расширения `_ssl` и `_hashlib`.
- macOS: `HoH.app/Contents/Frameworks/libssl.3.dylib`, `libcrypto.3.dylib`.
- Первоисточник: <https://www.openssl.org/source/license.html>

OpenSSL 3.x распространяется по Apache-2.0 — той же лицензии, что и HoH, поэтому
[`LICENSE`](LICENSE) является текстом лицензии и для этого компонента.

## SQLite — **общественное достояние**

Используется модулем стандартной библиотеки `sqlite3`.

- Windows: `runtime/python/DLLs/sqlite3.dll`. На Linux и macOS вкомпилирован в
  модуль расширения `_sqlite3`.
- Первоисточник: <https://www.sqlite.org/copyright.html>

SQLite передан в общественное достояние и не накладывает обязательств по
уведомлению. Он перечислен здесь для полноты.

## zlib, bzip2, XZ Utils (liblzma) — **разрешительные**

Механизмы сжатия для `zipfile`, `gzip`, `bz2` и `lzma`. HoH использует их при
чтении и записи payload'ов обновления.

- У zlib и bzip2 собственные разрешительные лицензии; liblzma из XZ Utils
  находится в общественном достоянии.
- Первоисточники: <https://zlib.net/zlib_license.html> ·
  <https://sourceware.org/bzip2/> · <https://tukaani.org/xz/>

## libffi — **MIT**

Обеспечивает работу модуля `ctypes`, через который HoH на Windows включает
поддержку масштабирования интерфейса под DPI экрана.

- Первоисточник: <https://github.com/libffi/libffi/blob/master/LICENSE>

## Expat — **MIT**

Обеспечивает работу XML-парсеров стандартной библиотеки.

- Первоисточник:
  <https://github.com/libexpat/libexpat/blob/master/expat/COPYING>

## mpdecimal — **BSD-2-Clause**

Обеспечивает работу модуля `decimal` стандартной библиотеки.

- Первоисточник: <https://www.bytereef.org/mpdecimal/>

## Загрузчик PyInstaller — **GPL-2.0-or-later с исключением для сборок**

Только macOS. `HoH.app/Contents/MacOS/HoH` — исполняемый файл, замороженный
PyInstaller, и загрузчик PyInstaller вкомпилирован в него.

- Первоисточник:
  <https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt>

В лицензии PyInstaller есть явное исключение: загрузчик разрешено линковать в
замороженное приложение, выпускаемое на **любых** условиях, включая проприетарные.
Именно поэтому его включение не делает HoH GPL. Кодом PyInstaller является только
загрузчик; в остальном PyInstaller не входит в поставляемое приложение и не
является зависимостью исходного кода HoH.

## Чего здесь намеренно нет

- **Агенты.** HoH запускает Codex, Claude Code, Hermes, aider и остальных как
  отдельные процессы по документированным протоколам. Ни один из них не входит ни
  в один пакет HoH, HoH не линкуется с ними и не распространяет их. Нужных
  агентов пользователь ставит сам, на условиях их собственных проектов.
- **Модели.** Веса моделей не поставляются. HoH обращается к тем адресам, которые
  настроил оператор.
- **Инструменты сборки.** Inno Setup, PlantUML и PyInstaller используются для
  выпуска и документирования релиза. За исключением описанного выше загрузчика
  PyInstaller, ничего из их кода до пользователя не доходит.

## Если в этом списке ошибка

Если компонент пропущен, приписан не тому или указан с неверной лицензией,
заведите issue на <https://github.com/john7ross/HoH/issues>. Ошибки в лицензиях
считаются дефектами, а не бумажной работой: их исправляют и пересобирают релиз.
