"""Run with the Freqtrade interpreter: python -m unittest discover -s tests -p test_replay_worker.py."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from freqtrade_bridge.offline_worker import ReplaySession

DATA = Path(__file__).resolve().parents[2] / 'freqtrade/user_data/data/binance/futures'

class ReplayRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'replay.json'
        self.session = ReplaySession(DATA, self.path)

    def test_pending_step_survives_restart_without_skipping_candle(self):
        observation = self.session.step()
        resumed = ReplaySession(DATA, self.path)
        self.assertTrue(resumed.pending)
        self.assertEqual(resumed.step()['observation_id'], observation['observation_id'])
        resumed.acknowledge(observation['observation_id'], {'status':'no_signal'})
        self.assertTrue(resumed.acknowledge(observation['observation_id'], {})['duplicate'])
        self.assertEqual(resumed.step()['cursor'], observation['cursor'] + 1)

    def test_only_past_candles_are_exposed(self):
        observation = self.session.snapshot()
        self.assertLessEqual(len(observation['candles']), 200)
        self.assertTrue(all(candle['timestamp'] < observation['as_of'] for candle in observation['candles']))
        self.assertEqual(observation['source'], 'historical_replay')

    def test_transient_windows_lock_is_retried(self):
        replace = os.replace
        attempts = []
        def transient(source, target):
            attempts.append(1)
            if len(attempts) == 1: raise PermissionError('temporary file scanner lock')
            return replace(source, target)
        with patch('freqtrade_bridge.offline_worker.os.replace', side_effect=transient):
            observation = self.session.step()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(json.loads(self.path.read_text())['cursor'], observation['cursor'])
