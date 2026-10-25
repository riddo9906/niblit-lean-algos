"""Packaged entry point; keep stdlib signal imports outside the bridge folder."""
from freqtrade_bridge.offline_worker import main
if __name__ == '__main__': main()
