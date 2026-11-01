"""Dry-run launch invariants; no network or trading services are started."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dry_launcher', Path(__file__).resolve().parents[1] / 'scripts/windows_freqtrade_dry_run.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class DryRunLauncherTests(unittest.TestCase):
    def test_prepare_reasserts_dry_run_and_preserves_api_credentials(self):
        with tempfile.TemporaryDirectory(prefix='Niblit moved ') as folder:
            output = Path(folder)
            config, path, userdir = launcher.prepare(output)
            password = config['api_server']['password']
            config['dry_run'] = False
            config['exchange']['key'] = 'must-not-survive'
            path.write_text(json.dumps(config))
            config, _, _ = launcher.prepare(output)
            self.assertIs(config['dry_run'], True)
            self.assertNotIn('protections', config)
            self.assertEqual(config['exchange']['key'], '')
            self.assertEqual(config['api_server']['password'], password)
            self.assertEqual(config['api_server']['listen_port'], 18080)
            self.assertEqual(config['api_server']['listen_ip_address'], '127.0.0.1')
            self.assertTrue(userdir.is_dir())
            self.assertNotEqual(password, 'changeme')

    def test_live_and_replay_overrides_do_not_leak_into_children(self):
        config = {'api_server': {'username': 'test', 'password': 'private'}}
        with patch.dict(os.environ, {'FREQTRADE__DRY_RUN': 'false',
                                    'FREQTRADE__EXCHANGE__KEY': 'secret',
                                    'NIBLIT_TRADING_MODE': 'live',
                                    'NIBLIT_MARKET_DATA_PROVIDER': 'replay'}):
            env = launcher.child_environment(config, Path('output'))
        self.assertNotIn('FREQTRADE__DRY_RUN', env)
        self.assertNotIn('FREQTRADE__EXCHANGE__KEY', env)
        self.assertEqual(env['NIBLIT_TRADING_MODE'], 'observe')
        self.assertEqual(env['NIBLIT_MARKET_DATA_PROVIDER'], 'ccxt')
        self.assertEqual(env['NIBLIT_FREQTRADE_ALLOW_EXECUTION'], 'false')

    def test_command_uses_selected_interpreter_and_explicit_dry_run(self):
        command = launcher.command(Path('moved folder/python.exe'), Path('a/config.json'), Path('a/user_data'))
        self.assertEqual(command[:3], ['moved folder\\python.exe' if os.name == 'nt' else 'moved folder/python.exe', '-m', 'freqtrade'])
        self.assertIn('--dry-run', command)
        self.assertIn('--strategy-path', command)

    def test_status_rejects_a_live_instance_before_other_calls(self):
        with patch.object(launcher, 'request_json', return_value={'dry_run': False}) as request:
            with self.assertRaisesRegex(RuntimeError, 'not verified dry-run'):
                launcher.status({})
            self.assertEqual(request.call_count, 1)

    def test_failed_child_stops_owned_services_and_records_failure(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            config, path, userdir = launcher.prepare(output)
            dead, alive = MagicMock(), MagicMock()
            dead.poll.return_value = 1
            alive.poll.return_value = None
            with patch.object(launcher, 'preflight'), patch.object(launcher.socket, 'socket'), \
                    patch.object(launcher.subprocess, 'Popen', side_effect=[dead, alive]):
                with self.assertRaisesRegex(RuntimeError, 'service exited'):
                    launcher.run(Path('python.exe'), config, path, userdir, output, True)
            alive.terminate.assert_called_once()
            alive.wait.assert_called_once()
            dead.terminate.assert_not_called()
            report = json.loads((output / 'verification.json').read_text())
            self.assertIs(report['ready'], False)


if __name__ == '__main__':
    unittest.main()
