import copy
import json
import unittest

import validate_profiles


class ProfilesVectorsTests(unittest.TestCase):
    def test_profiles_v1_vectors(self):
        self.assertEqual(validate_profiles.main(), 0)

    def test_numeric_policy_boundary_and_unknown(self):
        policy = {"maximumAge": 13, "unknownRatings": "block"}
        self.assertTrue(validate_profiles.policy_allows(policy, 13))
        self.assertFalse(validate_profiles.policy_allows(policy, 14))
        self.assertFalse(validate_profiles.policy_allows(policy, None))
        self.assertTrue(validate_profiles.policy_allows(policy, None, approved=True))
        with self.assertRaisesRegex(ValueError, "invalid_minimum_age"):
            validate_profiles.policy_allows(policy, 19)

    def test_policy_product_order(self):
        stricter_age = {"maximumAge": 10, "unknownRatings": "allow"}
        stricter_unknown = {"maximumAge": 13, "unknownRatings": "block"}
        self.assertEqual(validate_profiles.policy_relation(stricter_age, stricter_unknown), "incomparable")

    def test_preference_partition_keeps_unknowns_in_household(self):
        hidden = {"value": {"bool": True}, "at": [1, 0, "a1b2c3d4e5f60718"]}
        future = {"value": {"string": "kept"}, "at": [2, 0, "a1b2c3d4e5f60718"]}
        maturity = {"value": {"string": "r"}, "at": [3, 0, "a1b2c3d4e5f60718"]}
        self.assertEqual(validate_profiles.partition_preferences({
            "den.hideWatched": hidden,
            "future.preference": future,
            "den.maturityCeiling": maturity,
        }), {"household": {"future.preference": future},
             "owner": {"den.hideWatched": hidden}, "ownerMaturity": maturity})
        with self.assertRaisesRegex(ValueError, "invalid_preferences"):
            validate_profiles.partition_preferences([])

    def test_kid_discovery_uses_audience_intent_and_teen_band(self):
        child = {"maximumAge": 8, "unknownRatings": "block"}
        teen = {"maximumAge": 13, "unknownRatings": "block"}
        self.assertTrue(validate_profiles.kid_discovery_relevant(child, 0.5, None))
        self.assertFalse(validate_profiles.kid_discovery_relevant(child, None, 0.99))
        self.assertTrue(validate_profiles.kid_discovery_relevant(teen, None, 0.5))
        self.assertFalse(validate_profiles.kid_discovery_relevant(teen, None, None))

    def test_approval_merge_is_conservative_and_preserves_extensions(self):
        stamp = [1800000013000, 0, "a1b2c3d4e5f60718"]
        allow = {"value": "allow", "at": stamp, "future": "kept-if-winner"}
        tombstone = {"value": "tombstone", "at": stamp, "future": "kept"}
        self.assertEqual(validate_profiles.merge_approval(allow, tombstone), tombstone)
        self.assertEqual(validate_profiles.merge_approval(tombstone, allow), tombstone)

    def test_approval_document_unknowns_follow_newest_stamp(self):
        older = [1800000010000, 0, "a1b2c3d4e5f60718"]
        newer = [1800000011000, 0, "0f1e2d3c4b5a6978"]
        expected = {"future": "préservé"}
        self.assertEqual(validate_profiles.merge_unknown_set(older, {"future": "old"}, newer, expected), expected)
        self.assertEqual(validate_profiles.merge_unknown_set(newer, expected, older, {"future": "old"}), expected)

    def test_losing_migration_candidate_is_cleaned_before_adoption(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        case = {
            "state": "ready-to-commit", "observed": "committed",
            "directory": vectors["validDirectories"][0]["document"],
            "candidateAttempt": "fedcba9876543210fedcba9876543210",
            "candidateKey": "REREREREREREREREREREREREREREREREREREREREREQ=",
            "winnerVerified": True, "candidateRetired": False,
        }
        self.assertEqual(validate_profiles.migration_next(case), "retire-losing-candidate")
        case["candidateRetired"] = True
        self.assertEqual(validate_profiles.migration_next(case), "activate")

    def test_cleanup_never_targets_winner(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        case = {
            "state": "cleaning-loser", "observed": "committed",
            "directory": vectors["validDirectories"][0]["document"],
            "candidateAttempt": "fedcba9876543210fedcba9876543210",
            "candidateKey": "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI=",
            "winnerVerified": True,
        }
        self.assertEqual(validate_profiles.migration_next(case), "cleanup_targets_winner")

    def test_storage_transaction_is_durable_and_not_the_sync_generation(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        storage = vectors["migrationStorageContract"]
        self.assertEqual(storage["publicSyncGenerationType"], "opaque-string")
        self.assertEqual(storage["transactionGenerationType"], "safe-integer")
        self.assertFalse(storage["sameGenerationNamespace"])
        self.assertEqual(storage["transactionGenerationAllocation"],
                         "monotone-per-household-on-visible-mutation")
        self.assertTrue(storage["open"]["reopenReturnsSameFence"])
        self.assertTrue(storage["open"]["survivesServerRestart"])
        self.assertFalse(storage["open"]["idleExpiry"])
        self.assertTrue(storage["open"]["ordinaryWritesRemainAvailable"])
        self.assertTrue(storage["open"]["concurrentAttemptsCommitByFence"])
        self.assertTrue(storage["open"]["concurrentAttemptsOnSameSnapshotShareGeneration"])
        self.assertTrue(storage["open"]["restoreNeverReusesGeneration"])

    def test_atomic_retirement_requires_the_exact_live_directory_set(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        storage = vectors["migrationStorageContract"]
        directory = vectors["validDirectories"][0]["document"]
        evidence = copy.deepcopy(storage["retirementBase"])
        self.assertEqual(validate_profiles.retirement_result(evidence, directory), "retired")
        evidence["protectedLibraryIds"].pop()
        with self.assertRaisesRegex(ValueError, "incomplete_protection"):
            validate_profiles.retirement_result(evidence, directory)

    def test_restore_is_fresh_fenced_durable_and_never_lowers_wire_five(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        evidence = copy.deepcopy(vectors["migrationStorageContract"]["restoreBase"])
        self.assertNotEqual(evidence["recoveryAttempt"], evidence["backup"]["attempt"])
        self.assertEqual(validate_profiles.restore_result(evidence), "restored")
        self.assertEqual(evidence["result"]["generation"], evidence["authority"]["generation"] + 1)
        self.assertEqual(evidence["result"]["wireMin"], 5)

        evidence["currentAuthority"]["head"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "migration_fence_moved"):
            validate_profiles.restore_result(evidence)

    def test_restore_lost_response_resolves_by_terminal_lookup(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        evidence = copy.deepcopy(vectors["migrationStorageContract"]["restoreBase"])
        self.assertTrue(evidence["terminalLookupRetained"])
        self.assertTrue(evidence["identicalRetryReturnsSameResult"])
        self.assertEqual(evidence["conflictTerminalState"], "superseded")
        self.assertEqual(evidence["unknownResolution"],
                         ["lookup-attempt", "fresh-authenticated-read"])
        first = copy.deepcopy(evidence["result"])
        evidence["retry"] = True
        self.assertEqual(validate_profiles.restore_result(evidence), "restored")
        self.assertEqual(evidence["result"], first)

    def test_reset_waits_for_every_live_profile(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        profiles = copy.deepcopy(vectors["resetProfiles"])
        owner = profiles["00112233445566778899aabbccddeeff"]
        owner["verified"] = True
        owner["copiedHead"] = "a" * 64
        case = {"householdCommitted": False, "oldHouseholdRetired": False,
                "profiles": list(profiles.values())}
        self.assertEqual(validate_profiles.validate_reset(
            case, vectors["validDirectories"][0]["document"], vectors["resetFence"]),
            {"effect": "move-live-profiles",
             "profiles": ["11112222333344445555666677778888", "9999aaaabbbbccccddddeeeeffff0000"]})


if __name__ == "__main__":
    unittest.main()
