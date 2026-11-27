import unittest

import validate_follows

NOW = 1_795_161_600_000


def mark(t, value="read"):
    return {"value": {"string": value}, "at": [t, 0, "a1b2c3d4e5f60718"]}


class FollowsVectorsTests(unittest.TestCase):
    def test_follows_v1_vectors(self):
        self.assertEqual(validate_follows.main(), 0)

    def test_writer_keeps_the_newest_two_thousand_marks(self):
        values = {f"episode:tv:{n}:1:1": mark(NOW - n) for n in range(1, 2_003)}
        kept = validate_follows.pruned(values, NOW)
        self.assertEqual(len(kept), 2_000)
        self.assertIn("episode:tv:1:1:1", kept)
        self.assertNotIn("episode:tv:2002:1:1", kept)

    def test_derivation_is_deterministic_across_input_order(self):
        follows = {
            "tv:2": {"value": {"int": 1_794_312_000_000}, "at": [1, 0, "a"]},
            "tv:1": {"value": {"int": 1_794_312_000_000}, "at": [1, 0, "a"]},
        }
        airing = {
            "1": {"source": "tvmaze", "previous": {"season": 1, "episode": 1, "date": "2026-11-19"}},
            "2": {"source": "tvmaze", "previous": {"season": 1, "episode": 1, "date": "2026-11-19"}},
        }
        forward = validate_follows.derive("2026-11-20", follows, {"airing": airing})
        backward = validate_follows.derive(
            "2026-11-20", dict(reversed(follows.items())), {"airing": dict(reversed(airing.items()))}
        )
        self.assertEqual(forward, backward)
        self.assertEqual([event["id"] for event in forward["events"]], ["episode:tv:1:1:1", "episode:tv:2:1:1"])


if __name__ == "__main__":
    unittest.main()
