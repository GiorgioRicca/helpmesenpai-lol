"""Crea l'eseguibile: `uv run --with pyinstaller python packaging/build.py`.

Su Windows produce dist/lolpick/ (lolpick.exe + scorciatoie .bat) e dist/lolpick-windows.zip.
PyInstaller non fa cross-compilazione: il .exe si crea solo da Windows (o dal workflow GitHub Actions).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
OUT = DIST / "lolpick"
DATA = ROOT / "lolpick" / "data"
SHORTCUTS = {  # doppio clic su Windows
    "Aggiorna dati patch.bat": "--update",
    "Demo.bat": "--demo",
    "Senza LLM.bat": "--no-llm",
}


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    data_args = [f"--add-data={DATA / name}{os.pathsep}lolpick/data"
                 for name in ["champions.csv", "ddragon_cache.json", "meta.json", "matchups.json", "laning.json"] if (DATA / name).exists()]
    PyInstaller.__main__.run([
        str(ROOT / "packaging" / "entry.py"),
        "--name=lolpick", "--onefile", "--console", "--noconfirm",
        f"--distpath={OUT}", f"--workpath={ROOT / 'build'}", f"--specpath={ROOT / 'build'}",
        *data_args,
    ])
    # Copia modificabile dei dati accanto all'eseguibile (altrimenti la crea lui al primo avvio).
    shutil.copytree(DATA, OUT / "data", ignore=shutil.ignore_patterns("*.numbers", ".DS_Store"))
    if sys.platform == "win32":
        for name, flag in SHORTCUTS.items():
            (OUT / name).write_text(f'@echo off\r\ncd /d "%~dp0"\r\nlolpick.exe {flag}\r\n', encoding="utf-8")
    archive = shutil.make_archive(str(DIST / f"lolpick-{'windows' if sys.platform == 'win32' else sys.platform}"),
                                  "zip", OUT)
    print(f"\nPronto: {OUT}\nArchivio: {archive}")


if __name__ == "__main__":
    main()
