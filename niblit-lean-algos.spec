# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for the Niblit Lean trading runtime."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

REPO_ROOT = Path(SPECPATH).resolve()
datas = []
for name in ("algorithms", "niblit_bridge", "lean.json", "configs", "config"):
    source = REPO_ROOT / name
    if source.is_dir():
        datas.append((str(source), name))
    elif source.is_file():
        datas.append((str(source), "."))
hiddenimports = []
for package in (
    "niblit_bridge", "niblit_lean_algos", "adapters", "algorithms", "api",
    "cli", "config", "configs", "domain", "freqtrade_bridge",
    "freqtrade_strategies", "nibblebots", "persistence", "risk", "scripts", "services",
):
    try:
        hiddenimports.extend(collect_submodules(package))
    except ImportError:
        pass
hiddenimports.extend(["uvicorn", "fastapi", "ccxt", "httpx", "numpy", "pandas"])
a = Analysis(
    [str(REPO_ROOT / "main.py")], pathex=[str(REPO_ROOT)], binaries=[],
    datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=["pytest", "_pytest", "IPython", "jupyter", "tkinter", "matplotlib"],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="niblit-lean-algos",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False,
    upx_exclude=[], name="lean-algos",
)
