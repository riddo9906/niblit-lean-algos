from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve()
FREQ = Path(os.environ.get('NIBLIT_FREQTRADE_ROOT', ROOT.parent / 'freqtrade')).resolve()
# Freqtrade resolves pairlists/protections by scanning physical Python files.
# Hidden imports alone do not satisfy that plugin discovery contract.
datas = collect_data_files('freqtrade', include_py_files=True) + copy_metadata('freqtrade')
datas += [(str(ROOT / 'configs/freqtrade_config_dry_run.json'), 'profiles'),
          (str(ROOT / 'configs/freqtrade_config_binance.json'), 'profiles'),
          (str(ROOT / 'freqtrade_strategies'), 'strategies')]
for symbol in ('BTC', 'ETH'):
    data = FREQ / 'user_data/data/binance/futures' / f'{symbol}_USDT_USDT-1h-futures.feather'
    if not data.is_file():
        raise SystemExit(f'Recorded market data missing: {data}')
    datas.append((str(data), 'data'))
if (FREQ / 'LICENSE').is_file():
    datas.append((str(FREQ / 'LICENSE'), 'licenses/freqtrade'))
hidden = ['pyarrow.feather', 'talib', 'scipy', 'uvicorn', 'pandas_ta']
for package in ('freqtrade', 'freqtrade_strategies', 'freqtrade_bridge', 'talib'):
    hidden += collect_submodules(package)
a = Analysis([str(ROOT / 'freqtrade_strategy_worker.py')], pathex=[str(ROOT), str(FREQ)],
    binaries=[], datas=datas, hiddenimports=hidden, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=['pytest', '_pytest', 'IPython', 'jupyter', 'tkinter', 'matplotlib', 'torch'],
    noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='niblit-freqtrade-worker',
    debug=False, strip=False, upx=False, console=True, contents_directory='.')
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name='freqtrade')
