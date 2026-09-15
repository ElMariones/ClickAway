# Third-party notices

ClickAway's own source and logo are MIT licensed. Its dependencies retain their
respective licenses. Packaged applications use a directory of dynamically loaded
libraries; these can be replaced with compatible modified builds.

- **Outfit**, by the Outfit project authors: SIL Open Font License 1.1.
  The font and its full license are in `clickaway/assets/`.
  [Source](https://github.com/google/fonts/tree/main/ofl/outfit).
- **Qt / PySide6 / Shiboken**: LGPLv3 / GPLv3 / commercial licenses, as applicable
  to the individual included components. ClickAway uses the LGPL-compatible Core,
  Gui, Widgets and SVG runtime components through PySide6 and does not statically
  link Qt. Upstream sources and licensing information:
  [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/),
  [Qt sources](https://code.qt.io/),
  [Qt licenses](https://doc.qt.io/qt-6/licenses-used-in-qt.html).
  Wheel metadata and licenses are included in packaged app dependencies.
- **cryptography**, **cffi**, and their dependencies: upstream Apache/BSD/MIT
  licenses, with OpenSSL under its upstream license.
  [Source](https://github.com/pyca/cryptography).
- **PyObjC**: MIT license. [Source](https://github.com/ronaldoussoren/pyobjc).
- **CPython**: Python Software Foundation license.
  [Source and license](https://github.com/python/cpython).
- **PyInstaller bootloader**: GPL with the exception permitting distribution of
  bundled applications under their own license.
  [License](https://pyinstaller.org/en/stable/license.html).

Builds can be reproduced from this repository with the dependencies specified in
`pyproject.toml`. Qt libraries are distributed as separate files inside the app;
ClickAway does not prohibit replacing them or reverse engineering modifications
for debugging those libraries.
