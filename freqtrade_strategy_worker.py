"""Packaged replay worker and real Freqtrade CLI, selected explicitly."""
import sys

if __name__ == '__main__':
    if '--engine' in sys.argv:
        sys.argv.remove('--engine')
        from freqtrade.main import main
    elif '--prepare-profile' in sys.argv:
        sys.argv.remove('--prepare-profile')
        from freqtrade_bridge.desktop_profile import main
    else:
        from freqtrade_bridge.offline_worker import main
    main()
