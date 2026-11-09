import copy
import json
import unittest

import validate_playback_receipts as receipts


class PlaybackReceiptV2Tests(unittest.TestCase):
    def setUp(self):
        self.vectors = json.loads(receipts.VECTORS.read_text())
        self.authority = self.vectors["derivation"]["authority"]
        self.fixture = self.vectors["proofs"][0]

    def test_committed_vectors_validate(self):
        receipts.validate()

    def test_exact_wrapper_accepts_and_every_bound_field_changes_the_proof(self):
        operation = self.fixture["operation"]
        wrapper = {"authority": self.authority, "operation": operation, "proof": self.fixture["proof"]}
        receipts.validate_wrapper(wrapper, operation["household"], operation["issuedAt"])
        mutations = {
            "household": "f" * 32,
            "route": "e" * 32,
            "binding": "d" * 32,
            "generation": 2,
            "issuedAt": operation["issuedAt"] + 1,
            "nonce": "c" * 32,
            "op": "drain",
        }
        for field, value in mutations.items():
            changed = copy.deepcopy(wrapper)
            changed["operation"][field] = value
            if field == "op":
                changed["operation"].pop("generation")
            with self.assertRaises(ValueError, msg=field):
                receipts.validate_wrapper(changed, operation["household"], operation["issuedAt"])

    def test_wrong_authority_member_time_and_unknown_fields_fail_closed(self):
        operation = self.fixture["operation"]
        wrapper = {"authority": self.authority, "operation": operation, "proof": self.fixture["proof"]}
        wrong = copy.deepcopy(wrapper)
        wrong["authority"] = "_" * 43
        with self.assertRaises(ValueError):
            receipts.validate_wrapper(wrong, operation["household"], operation["issuedAt"])
        with self.assertRaisesRegex(ValueError, "not_a_member"):
            receipts.validate_wrapper(wrapper, "f" * 32, operation["issuedAt"])
        with self.assertRaisesRegex(ValueError, "stale_proof"):
            receipts.validate_wrapper(wrapper, operation["household"], operation["issuedAt"] + 300_001)
        extra = copy.deepcopy(wrapper)
        extra["operation"]["profile"] = "owner"
        with self.assertRaisesRegex(ValueError, "invalid_operation_fields"):
            receipts.validate_wrapper(extra, operation["household"], operation["issuedAt"])

    def test_owner_kid_visitor_replacement_deletion_rotation_and_activation_cases(self):
        decisions = (
            ("authorityCases", receipts.authority_outcome),
            ("lifecycleCases", receipts.lifecycle_outcome),
            ("activationCases", receipts.activation_outcome),
        )
        for group, decide in decisions:
            for case in self.vectors[group]:
                with self.subTest(case=case["name"]):
                    self.assertEqual(decide(case), case["expected"])

    def test_exact_aad_and_plaintext_bind_every_route_member(self):
        envelope = self.vectors["envelope"]
        self.assertEqual(receipts.canonical_json(envelope["aad"]), envelope["aadJcs"])
        self.assertEqual(receipts.canonical_json(envelope["receipt"]), envelope["receiptJcs"])
        for field in ("v", "household", "route", "binding", "generation", "kid", "id"):
            with self.subTest(field=field):
                self.assertEqual(envelope["aad"][field], envelope["receipt"][field])

    def test_replay_wrong_route_expiry_newer_progress_and_runtime_fence_drive_ack(self):
        for case in self.vectors["applyCases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(receipts.apply_outcome(case), case["expected"])
        for case in self.vectors["ackCases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(receipts.ack_ceiling(case["start"], case["results"]), case["expected"])

    def test_operation_members_are_exact_for_each_protocol_operation(self):
        common = {
            "v": 2,
            "household": "0" * 32,
            "route": "1" * 32,
            "binding": "2" * 32,
            "generation": 1,
            "issuedAt": 1,
            "nonce": "3" * 32,
        }
        additions = {
            "register": {"activity": "4" * 32, "kid": "5" * 32, "public": "x"},
            "rebind": {"previousActivity": "4" * 32, "activity": "5" * 32},
            "rotate": {"kid": "5" * 32, "public": "x"},
            "revoke": {},
            "playback": {"request": "6" * 64},
            "drain": {},
            "ack": {"upTo": 1},
            "activate": {},
        }
        for op, fields in additions.items():
            operation = {**common, "op": op, **fields}
            receipts.validate_operation(operation)
            operation["future"] = True
            with self.assertRaisesRegex(ValueError, "invalid_operation_fields"):
                receipts.validate_operation(operation)


if __name__ == "__main__":
    unittest.main()
