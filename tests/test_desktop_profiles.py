import json
from pathlib import Path
import tempfile
import unittest

from freqtrade_bridge.desktop_profile import prepare_profile

ROOT = Path(__file__).resolve().parents[1]


class DesktopProfileTests(unittest.TestCase):
    def test_live_profile_is_stopped_without_credentials_or_rpc_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private' / 'live.json'
            result = prepare_profile(path, 'live', ROOT)
            config = json.loads(path.read_text())
            self.assertFalse(config['dry_run'])
            self.assertEqual(config['initial_state'], 'stopped')
            self.assertEqual(config['exchange']['key'], '')
            self.assertEqual(config['exchange']['secret'], '')
            self.assertFalse(config['force_entry_enable'])
            self.assertFalse(result['engine_started'])
            self.assertNotIn('protections', config)
            self.assertTrue(Path(config['strategy_path']).is_dir())
            self.assertEqual(config['api_server']['listen_port'], 18080)

    def test_dry_profile_and_live_database_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            dry, live = Path(folder) / 'dry.json', Path(folder) / 'live.json'
            prepare_profile(dry, 'dry-run', ROOT)
            prepare_profile(live, 'live', ROOT)
            a, b = json.loads(dry.read_text()), json.loads(live.read_text())
            self.assertTrue(a['dry_run'])
            self.assertNotEqual(a['db_url'], b['db_url'])
            self.assertNotEqual(a['api_server']['password'], b['api_server']['password'])

    def test_existing_private_configuration_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'live.json'
            path.write_text('private original')
            with self.assertRaises(FileExistsError):
                prepare_profile(path, 'live', ROOT)
            self.assertEqual(path.read_text(), 'private original')


if __name__ == '__main__':
    unittest.main()
