# Windows live-data Freqtrade dry-run

Verified on Windows on 2026-10-26 with installed Freqtrade 2026.7-dev:
configuration/strategy preflight, five launcher tests, 26 adapter tests, and
the live `verify` command passed. The latter confirmed Binance candles,
Freqtrade RUNNING in dry-run, and a healthy Niblit Freqtrade adapter. Test
services were stopped afterwards. This does not validate profitability,
complete trade lifecycle, or Core's cognitive-envelope publisher.

Double-click `Start-Freqtrade-Dry-Run.cmd`. Requires the installed sibling
`freqtrade/.venv/Scripts/python.exe` and this repository. It resolves locations
relative to its source, not the shell's current folder. Keep both repositories
together when relocating; recreate the virtual environment after a move.

This starts the real Freqtrade engine with `NiblitAiMaster`, Binance spot market
data, a simulated 10,000 USDT wallet, and the Niblit trading adapter. It does
not run the historical replay worker. Internet access to Binance is required.
Exchange keys are empty and live trading cannot be selected in this launcher.

- Freqtrade API: `http://127.0.0.1:18080` (avoids llama.cpp on 8080).
- Niblit trading API: `http://127.0.0.1:8100`.
- State/logs/config: `build/windows-freqtrade-dry-run/`, ignored by Git.
- `config.json` contains generated private local API credentials; do not share it.
- Ctrl+C stops the two processes this launcher owns. Simulated trades persist
  in `trades.dryrun.sqlite`; an interrupted simulated position may remain open.

Close the older Niblit replay/paper runtime before starting; an occupied port
causes an error rather than killing or attaching to an unidentified service.

The adapter runs in **observe** mode: Freqtrade's own strategy can place
simulated orders, but Niblit RPC force-entry/exit remains disabled. Strategy
governance applies when a valid envelope is available. The existing strategy
permits technical-strategy entries when the envelope is missing; this launcher
does not change that policy. A running bot does not promise trades or profits.
The profile's signal file is `build/windows-freqtrade-dry-run/niblit-signal.json`.
Connecting Core's cognitive envelope publisher to that exact file remains a
separate integration check; do not invent an approval envelope for it.

From PowerShell in this repository:

```powershell
& ..\freqtrade\.venv\Scripts\python.exe scripts\windows_freqtrade_dry_run.py check
& ..\freqtrade\.venv\Scripts\python.exe scripts\windows_freqtrade_dry_run.py verify
& ..\freqtrade\.venv\Scripts\python.exe scripts\windows_freqtrade_dry_run.py status
```

`check` loads the actual Freqtrade config and strategy without starting services.
The removed configuration-level protections setting is migrated to the
strategy's `protections` property, preserving the StoplossGuard parameters.
`verify` starts both services, checks authenticated dry-run identity, running
state, adapter health and live market-data health, then stops them. It writes
`verification.json`; only `ready: true` confirms the full readiness check.
`status` reads the running services. An existing Niblit UI that uses trading
port 8100 can read this adapter; the old testing launcher hardcodes the paper
backend and should not be used to start this profile.

This is a source-based Windows launch build, not yet a standalone frozen EXE.
The existing packaged Freqtrade worker is a historical replay service, not a
packaged live-data Freqtrade engine. Ubuntu requires its own environment/build.

References: [Freqtrade configuration](https://www.freqtrade.io/en/stable/configuration/)
and [REST API](https://www.freqtrade.io/en/stable/rest-api/).
