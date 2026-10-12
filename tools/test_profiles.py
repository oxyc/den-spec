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
        case = {
            "state": "ready-to-commit", "observed": "committed",
            "candidateLibrary": "loser", "winnerLibrary": "winner",
            "winnerVerified": True, "candidateRetired": False,
        }
        self.assertEqual(validate_profiles.migration_next(case), "retire-losing-candidate")
        case["candidateRetired"] = True
        self.assertEqual(validate_profiles.migration_next(case), "activate")

    def test_cleanup_never_targets_winner(self):
        case = {
            "state": "cleaning-loser", "observed": "committed",
            "candidateLibrary": "winner", "winnerLibrary": "winner",
            "winnerVerified": True, "cleanupRequested": True,
        }
        self.assertEqual(validate_profiles.migration_next(case), "cleanup_targets_winner")

    def test_reset_waits_for_every_live_profile(self):
        case = {
            "householdCommitted": False, "oldHouseholdRetired": False,
            "profiles": [
                {"oldKey": "a", "newKey": "b", "oldVersion": 1, "newVersion": 2,
                 "deleted": False, "verified": True, "oldRetired": False},
                {"oldKey": "c", "newKey": "d", "oldVersion": 1, "newVersion": 2,
                 "deleted": False, "verified": False, "oldRetired": False},
            ],
        }
        self.assertEqual(validate_profiles.validate_reset(case), "move-live-profiles")


if __name__ == "__main__":
    unittest.main()
