import unittest

from freqtrade_strategies.advisor_protocol import parse_advisor_votes, summarize_debate


class AdvisorProtocolTests(unittest.TestCase):
    def test_summarize_debate_computes_consensus_and_disagreement(self):
        envelope = {
            "advisors": {
                "votes": {
                    "trend": {"direction": "BUY", "confidence": 0.9, "uncertainty": 0.1},
                    "mean_rev": {"direction": "SELL", "confidence": 0.6, "uncertainty": 0.2},
                    "liquidity": {"direction": "BUY", "confidence": 0.7, "uncertainty": 0.3},
                }
            }
        }
        votes = parse_advisor_votes(envelope)
        self.assertEqual(len(votes), 3)
        consensus = summarize_debate(envelope)
        self.assertEqual(consensus.direction, "BUY")
        self.assertGreater(consensus.model_consensus, 0.4)
        self.assertGreaterEqual(consensus.strategy_disagreement, 0.0)
        self.assertLessEqual(consensus.strategy_disagreement, 1.0)


if __name__ == "__main__":
    unittest.main()
