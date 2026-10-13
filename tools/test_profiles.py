import copy
import json
import math
import unittest

import validate_profiles


class ProfilesVectorsTests(unittest.TestCase):
    def test_profiles_v1_vectors(self):
        self.assertEqual(validate_profiles.main(), 0)

    def test_profile_capability_is_per_household_and_runtime_is_durable(self):
        legacy = {
            "enrollment": "prepare", "wireMin": 4, "directory": "absent",
            "request": "ordinary", "wire": 5, "authenticated": True,
        }
        self.assertEqual(validate_profiles.profile_capability(legacy),
                         {"state": "prepare", "effect": "serve-legacy"})
        committed = {**legacy, "enrollment": "off", "wireMin": 5, "directory": "valid"}
        self.assertEqual(validate_profiles.profile_capability(committed),
                         {"state": "runtime", "effect": "serve-runtime"})

    def test_profile_capability_fails_closed_at_wire_five(self):
        invalid = {
            "enrollment": "prepare", "wireMin": 5, "directory": "invalid",
            "request": "ordinary", "wire": 5, "authenticated": True,
        }
        with self.assertRaisesRegex(ValueError, "runtime_directory_required"):
            validate_profiles.profile_capability(invalid)
        invalid["wire"] = 4
        self.assertEqual(validate_profiles.profile_capability(invalid),
                         {"effect": "update-required", "requiredWire": 5})

    def test_profile_migration_transport_survives_invalid_directory_but_not_newer_wire(self):
        recovery = {
            "enrollment": "off", "wireMin": 5, "directory": "invalid",
            "request": "migration", "wire": 5, "authenticated": True,
        }
        self.assertEqual(validate_profiles.profile_capability(recovery),
                         {"effect": "migration-storage"})
        recovery["wireMin"] = 6
        self.assertEqual(validate_profiles.profile_capability(recovery),
                         {"effect": "update-required", "requiredWire": 6})

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

    def test_one_pin_action_resumes_exact_attempt_and_binds_commit_without_second_prompt(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        fixture = vectors["migrationAuthorization"]
        prepared = {
            "state": "awaiting-confirmation", "observed": "legacy",
            "attempt": fixture["intent"]["attempt"],
            "householdId": fixture["intent"]["householdId"],
            "splitAuthorization": copy.deepcopy(fixture["prepared"]),
            "fence": {"generation": 7, "head": "a" * 64},
            "observedFence": {"generation": 7, "head": "a" * 64},
            "backupVerified": True,
            "backupDigest": "b" * 64,
            "candidateKey": "REREREREREREREREREREREREREREREREREREREREREQ=",
            "ownerVerified": True, "candidateHead": "c" * 64,
            "stagedDigest": "d" * 64, "wireMin": 5,
        }
        self.assertEqual(validate_profiles.migration_next(prepared), "record-commit-binding")
        prepared["state"] = "ready-to-commit"
        with self.assertRaisesRegex(ValueError, "commit_authorization_required"):
            validate_profiles.migration_next(prepared)
        prepared["commitAuthorized"] = True
        prepared["splitAuthorization"] = copy.deepcopy(fixture["bound"])
        self.assertEqual(validate_profiles.migration_next(prepared), "commit-fenced-household")

        prepared["observedFence"]["head"] = "e" * 64
        self.assertEqual(validate_profiles.migration_next(prepared), "retire-losing-candidate")
        prepared["candidateRetired"] = True
        self.assertEqual(validate_profiles.migration_next(prepared), "invalidate-authorization")

    def test_commit_unknown_requires_lookup_then_fresh_authenticated_read(self):
        case = {
            "state": "commit-unknown", "observed": "legacy",
            "attempt": "0123456789abcdef0123456789abcdef",
            "householdId": "5e1ab5db12f9b30e640e3ee80fb3e072",
            "splitAuthorization": json.loads(validate_profiles.VECTORS.read_text())["migrationAuthorization"]["bound"],
            "fence": {"generation": 7, "head": "a" * 64},
            "observedFence": {"generation": 7, "head": "a" * 64},
            "commitAuthorized": True,
            "backupDigest": "b" * 64,
            "candidateKey": "REREREREREREREREREREREREREREREREREREREREREQ=",
            "candidateHead": "c" * 64, "ownerVerified": True,
            "stagedDigest": "d" * 64,
            "wireMin": 5,
        }
        self.assertEqual(validate_profiles.migration_next(case), "lookup-attempt")
        case["attemptLookup"] = "open"
        self.assertEqual(validate_profiles.migration_next(case), "read-household")
        case["householdFresh"] = True
        self.assertEqual(validate_profiles.migration_next(case), "retry-bound-commit")

        corrupt = {
            "state": "commit-unknown", "observed": "corrupt", "commitAuthorized": True,
            "attempt": case["attempt"], "householdId": case["householdId"],
            "splitAuthorization": case["splitAuthorization"],
            "fence": case["fence"], "backupDigest": case["backupDigest"],
            "candidateKey": case["candidateKey"], "candidateHead": case["candidateHead"],
            "ownerVerified": True,
            "stagedDigest": case["stagedDigest"], "wireMin": 5,
        }
        self.assertEqual(validate_profiles.migration_next(corrupt), "lookup-attempt")
        corrupt["attemptLookup"] = "committed"
        self.assertEqual(validate_profiles.migration_next(corrupt), "read-household")
        corrupt["householdFresh"] = True
        with self.assertRaisesRegex(ValueError, "terminal_household_mismatch"):
            validate_profiles.migration_next(corrupt)

    def test_cancel_reads_fresh_household_after_terminal_abort(self):
        fixture = json.loads(validate_profiles.VECTORS.read_text())["migrationAuthorization"]
        case = {
            "state": "awaiting-confirmation", "observed": "legacy",
            "attempt": fixture["intent"]["attempt"],
            "householdId": fixture["intent"]["householdId"],
            "splitAuthorization": fixture["prepared"],
            "fence": {"generation": 7, "head": "a" * 64},
            "observedFence": {"generation": 7, "head": "a" * 64},
            "backupVerified": True,
            "backupDigest": "b" * 64,
            "candidateKey": "REREREREREREREREREREREREREREREREREREREREREQ=",
            "ownerVerified": True, "candidateHead": "c" * 64,
            "stagedDigest": "d" * 64, "wireMin": 5,
            "cancelRequested": True, "attemptAborted": True, "attemptLookup": "aborted",
        }
        self.assertEqual(validate_profiles.migration_next(case), "read-household")
        case["householdFresh"] = True
        self.assertEqual(validate_profiles.migration_next(case), "retire-losing-candidate")

    def test_cancelled_same_source_retirement_requires_terminal_abort(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        storage = vectors["migrationStorageContract"]
        directory = vectors["validDirectories"][0]["document"]
        case = next(item for item in storage["retirementCases"]
                    if item["name"].startswith("an explicitly aborted attempt"))
        evidence = copy.deepcopy(storage["retirementBase"])
        for mutation in case["mutations"]:
            validate_profiles.mutate_pointer(evidence, mutation)
        self.assertEqual(validate_profiles.retirement_result(evidence, directory), "retired")
        evidence["attemptState"] = "open"
        with self.assertRaisesRegex(ValueError, "cancel_not_authorized"):
            validate_profiles.retirement_result(evidence, directory)

    def test_migration_digest_sorts_rows_and_rejects_duplicate_evidence(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        case = copy.deepcopy(vectors["migrationDigestCases"][0])
        canonical, digest = validate_profiles.migration_digest(case["kind"], case["input"])
        self.assertEqual(canonical, case["canonical"])
        self.assertEqual(digest, case["expect"])
        case["input"]["rows"][1]["k"] = case["input"]["rows"][0]["k"]
        with self.assertRaisesRegex(ValueError, "invalid_migration_digest"):
            validate_profiles.migration_digest(case["kind"], case["input"])

    def test_materialization_rejects_a_document_the_v4_writer_would_normalize(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        valid = copy.deepcopy(vectors["migrationMaterializeCases"][1]["input"])
        jcs, is_document = validate_profiles.materialize_identity(valid)
        self.assertTrue(is_document)
        self.assertEqual(json.loads(jcs), valid["row"])

        valid["row"]["watch"]["plays"]["8"] = 1008
        with self.assertRaisesRegex(ValueError, "invalid_materialize_input"):
            validate_profiles.materialize_identity(valid)

    def test_directory_constructor_returns_the_only_seal_ready_plaintext(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        result = validate_profiles.migration_construct_directory(
            copy.deepcopy(vectors["migrationDirectoryConstructorInput"]))
        self.assertEqual(set(result), {"directory", "jcs", "plaintext"})
        jcs = validate_profiles.decode_unpadded_base64url(result["jcs"])
        plaintext = validate_profiles.decode_unpadded_base64url(result["plaintext"])
        self.assertEqual(plaintext[:1], b"\x00")
        self.assertEqual(validate_profiles.inflate_document_plaintext(plaintext), jcs)
        self.assertEqual(jcs, validate_profiles.canonical_json(result["directory"]))
        self.assertEqual(validate_profiles.canonical_json(json.loads(jcs)), jcs)
        with self.assertRaisesRegex(ValueError, "invalid_document_plaintext"):
            validate_profiles.inflate_document_plaintext(plaintext + b"trailing")

        with self.assertRaisesRegex(ValueError, "unsupported_materialize_kind"):
            validate_profiles.materialize_identity({"name": "profiles", "row": result["directory"]})

    def test_directory_constructor_extensions_use_ecmascript_number_serialization(self):
        numbers = {
            "integerDouble": 1.0,
            "negativeZero": -0.0,
            "lowerExponent": 1e-7,
            "lowerFixed": 1e-6,
            "upperFixed": 1e20,
            "upperExponent": 1e21,
            "representative": [333333333.33333329, 1e30, 4.5, 0.002, 1e-27],
        }
        expected = (b'{"integerDouble":1,"lowerExponent":1e-7,"lowerFixed":0.000001,'
                    b'"negativeZero":0,"representative":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
                    b'"upperExponent":1e+21,"upperFixed":100000000000000000000}')
        self.assertEqual(validate_profiles.canonical_json(numbers), expected)
        self.assertEqual(validate_profiles.canonical_json(json.loads(expected)), expected)

        vectors = json.loads(validate_profiles.VECTORS.read_text())
        constructor = copy.deepcopy(vectors["migrationDirectoryConstructorInput"])
        constructor["owner"]["display"]["value"]["futureNumbers"] = numbers
        result = validate_profiles.migration_construct_directory(constructor)
        jcs = validate_profiles.decode_unpadded_base64url(result["jcs"])
        plaintext = validate_profiles.decode_unpadded_base64url(result["plaintext"])
        self.assertEqual(validate_profiles.inflate_document_plaintext(plaintext), jcs)
        self.assertEqual(validate_profiles.canonical_json(json.loads(jcs)), jcs)

    def test_jcs_rejects_non_finite_numbers(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_jcs_number"):
                validate_profiles.canonical_json({"future": value})

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
        self.assertEqual(evidence["open"]["purpose"], "restore")
        self.assertEqual(evidence["fence"], evidence["open"]["fence"])
        self.assertEqual(validate_profiles.restore_result(evidence), "restored")
        self.assertEqual(evidence["result"]["generation"], evidence["fence"]["generation"] + 1)
        self.assertEqual(evidence["result"]["wireMin"], 5)

        evidence["currentFence"]["head"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "migration_fence_moved"):
            validate_profiles.restore_result(evidence)

    def test_restore_requires_exact_fence_from_durable_recovery_open(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        evidence = copy.deepcopy(vectors["migrationStorageContract"]["restoreBase"])
        self.assertTrue(evidence["open"]["lookupReturnsSameOpen"])
        evidence["fence"]["rewrite"] = "not-the-opened-handle"
        with self.assertRaisesRegex(ValueError, "restore_fence_mismatch"):
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
