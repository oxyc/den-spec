import json
import subprocess
import unittest

import validate_play_results


class PlayResultsVectorsTests(unittest.TestCase):
    def test_vectors_validate(self):
        validate_play_results.validate()

    def test_generator_matches_committed_vectors(self):
        generated = subprocess.run(
            ["node", "tools/play-results-vectors.mjs"],
            cwd=validate_play_results.ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(json.loads(generated), json.loads(validate_play_results.VECTORS.read_text()))

    def test_each_request_has_its_own_address_and_key(self):
        one = "ffeeddccbbaa99887766554433221100"
        other = "ffeeddccbbaa99887766554433221101"
        self.assertNotEqual(validate_play_results.address(one), validate_play_results.address(other))
        self.assertNotEqual(validate_play_results.key(one), validate_play_results.key(other))
        self.assertNotEqual(validate_play_results.address(one), validate_play_results.key(one)[:32])

    def test_noncanonical_ids_are_refused(self):
        request_id = "ffeeddccbbaa99887766554433221100"
        for invalid in (request_id.upper(), request_id[:-1], request_id + "0", f" {request_id}"):
            with self.assertRaisesRegex(ValueError, "invalid_request_id"):
                validate_play_results.address(invalid)

    def test_a_receiver_writes_only_v1_results(self):
        def padded(body):
            text = json.dumps(body, separators=(",", ":"), sort_keys=True)
            return text + " " * (128 - len(text))

        self.assertEqual(
            validate_play_results.result(padded({"at": 1, "state": "playing", "v": 1}))["state"], "playing"
        )
        for bad, code in (
            (padded({"at": 1, "state": "playing", "v": 2}), "plaintext_version"),
            (padded({"at": "1", "state": "playing", "v": 1}), "plaintext_at"),
            (padded({"at": 1, "state": "started", "v": 1}), "plaintext_state"),
            (padded({"at": 1, "state": "failed", "v": 1}), "plaintext_reason"),
            (padded({"at": 1, "reason": "no_source", "state": "playing", "v": 1}), "plaintext_reason"),
            ('{"at":1,"state":"playing","v":1}', "plaintext_size"),
        ):
            with self.assertRaisesRegex(ValueError, code):
                validate_play_results.result(bad)

    def test_receipts_and_queue_state_are_not_playing_evidence(self):
        contract = (validate_play_results.ROOT / "wire" / "play-results-v1.md").read_text()
        for not_evidence in ("acknowledgement", "`receiver-ready`", "`Router.play`", "source lookup succeeding"):
            self.assertIn(not_evidence, contract)


if __name__ == "__main__":
    unittest.main()
