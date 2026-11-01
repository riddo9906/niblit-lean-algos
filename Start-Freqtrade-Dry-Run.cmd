@echo off
setlocal
set "FT_PYTHON=%~dp0..\freqtrade\.venv\Scripts\python.exe"
if not exist "%FT_PYTHON%" (
  echo Freqtrade Python not found beside this repository. See WINDOWS_FREQTRADE_DRY_RUN.md.
  pause
  exit /b 1
)
"%FT_PYTHON%" "%~dp0scripts\windows_freqtrade_dry_run.py" start
if errorlevel 1 pause
