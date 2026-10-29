import copy
import json
import math
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

    def test_seed_gives_only_keys_the_profile_lacks(self):
        stamp = [1, 0, "a1b2c3d4e5f60718"]
        region = {"value": {"string": "FI"}, "at": stamp}
        hidden = {"value": {"bool": True}, "at": stamp}
        cleared = {"value": None, "at": [2, 0, "a1b2c3d4e5f60718"]}
        household = {"den.watchRegion": region, "den.hideWatched": hidden}
        self.assertEqual(validate_profiles.seed_preferences({"household": household, "profile": None}),
                         {"values": household})
        self.assertEqual(validate_profiles.seed_preferences(
            {"household": household, "profile": {"den.hideWatched": cleared}}), {"values": {"den.watchRegion": region}})
        with self.assertRaisesRegex(ValueError, "invalid_preferences"):
            validate_profiles.seed_preferences({"household": [], "profile": None})

    def test_household_write_withholds_only_profile_scoped_values(self):
        stamp = [1, 0, "a1b2c3d4e5f60718"]
        simkl = {"value": {"string": "t"}, "at": stamp}
        tmdb = {"value": {"string": "k"}, "at": stamp}
        self.assertEqual(validate_profiles.household_write({"name": "title:movie:1"}), {"effect": "refuse"})
        self.assertEqual(validate_profiles.household_write({"name": "set:assistant"}), {"effect": "allow"})
        self.assertEqual(
            validate_profiles.household_write({"name": "set:keys", "values": {"simkl": simkl, "tmdb": tmdb}}),
            {"effect": "restrict", "values": {"tmdb": tmdb}})
        self.assertEqual(validate_profiles.household_write({"name": "set:keys", "values": {"simkl": simkl}}),
                         {"effect": "refuse"})
        self.assertEqual(validate_profiles.household_write({"name": "set:prefs", "values": {}}), {"effect": "refuse"})
        with self.assertRaisesRegex(ValueError, "invalid_household_write"):
            validate_profiles.household_write({"name": "set:prefs"})

    def test_household_audit_removes_and_rewrites_in_order(self):
        stamp = [1, 0, "a1b2c3d4e5f60718"]
        region = {"value": {"string": "FI"}, "at": stamp}
        hidden = {"value": {"bool": True}, "at": stamp}
        self.assertEqual(validate_profiles.household_audit([
            {"name": "dlv:simkl:1"},
            {"name": "set:prefs", "values": {"den.watchRegion": region, "den.hideWatched": hidden}},
            {"name": "profiles"},
        ]), {"remove": ["dlv:simkl:1", "set:prefs"], "rewrite": []})
        with self.assertRaisesRegex(ValueError, "invalid_household_write"):
            validate_profiles.household_audit([{"name": "profiles"}, {"name": "profiles"}])

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
        constructor = copy.deepcopy(vectors["initialDirectoryConstructorInput"])
        constructor["owner"]["display"]["value"]["futureNumbers"] = numbers
        result = validate_profiles.initial_directory_constructor(constructor)
        jcs = validate_profiles.decode_unpadded_base64url(result["jcs"])
        plaintext = validate_profiles.decode_unpadded_base64url(result["plaintext"])
        self.assertEqual(validate_profiles.inflate_document_plaintext(plaintext), jcs)
        self.assertEqual(validate_profiles.canonical_json(json.loads(jcs)), jcs)

    def test_profiles_materializer_accepts_only_stored_valid_roundtripping_directories(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        document = copy.deepcopy(vectors["validDirectories"][0]["document"])
        result = validate_profiles.profiles_materialize(document)
        self.assertEqual(result["directory"], document)
        jcs = validate_profiles.decode_unpadded_base64url(result["jcs"])
        plaintext = validate_profiles.decode_unpadded_base64url(result["plaintext"])
        self.assertEqual(jcs, validate_profiles.canonical_json(document))
        self.assertEqual(validate_profiles.inflate_document_plaintext(plaintext), jcs)
        without_migration = copy.deepcopy(document)
        without_migration.pop("migration")
        with self.assertRaisesRegex(ValueError, "profiles_migration_required"):
            validate_profiles.profiles_materialize(without_migration)

    def test_kid_create_journal_forces_fail_closed_policy_and_exact_cas(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        journal = copy.deepcopy(vectors["profileCreateJournal"])
        profile_id, _, entry = validate_profiles.create_journal_entry(journal)
        self.assertEqual(profile_id, journal["profile"])
        self.assertEqual(entry["policy"]["value"], {"maximumAge": 8, "unknownRatings": "block"})
        base = copy.deepcopy(vectors["validDirectories"][0]["document"])
        candidate = validate_profiles.apply_directory_operation(base, {
            "operation": "create", "profile": profile_id, "entry": entry,
        })
        planned = validate_profiles.profiles_create_next({
            "state": "ready", "journal": journal, "candidate": candidate,
            "authority": copy.deepcopy(vectors["profileCreateAuthority"]),
        })
        self.assertEqual({key: planned[key] for key in ("base", "generation")},
                         vectors["profileCreateAuthority"])
        self.assertEqual(planned["effect"], "cas-directory")

    def test_unknown_kid_create_result_is_lookup_by_directory_merge(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        journal = copy.deepcopy(vectors["profileCreateJournal"])
        profile_id, _, entry = validate_profiles.create_journal_entry(journal)
        base = copy.deepcopy(vectors["validDirectories"][0]["document"])
        candidate = validate_profiles.apply_directory_operation(base, {
            "operation": "create", "profile": profile_id, "entry": entry,
        })
        common = {
            "state": "cas-unknown", "journal": journal, "candidate": candidate,
            "authority": copy.deepcopy(vectors["profileCreateAuthority"]), "householdFresh": True,
        }
        adopted = validate_profiles.profiles_create_next({**common, "current": candidate})
        self.assertEqual(adopted["effect"], "adopt")
        retried = validate_profiles.profiles_create_next({**common, "current": base})
        self.assertEqual(retried["effect"], "cas-directory")

    def test_active_kid_create_journal_needs_fresh_directory_before_discard(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        journal = copy.deepcopy(vectors["profileCreateJournal"])
        profile_id, _, entry = validate_profiles.create_journal_entry(journal)
        base = copy.deepcopy(vectors["validDirectories"][0]["document"])
        candidate = validate_profiles.apply_directory_operation(base, {
            "operation": "create", "profile": profile_id, "entry": entry,
        })
        evidence = {
            "state": "active", "journal": journal, "candidate": candidate,
            "authority": copy.deepcopy(vectors["profileCreateAuthority"]), "householdFresh": False,
        }
        self.assertEqual(validate_profiles.profiles_create_next(evidence)["effect"], "read-directory")
        evidence.update({"householdFresh": True, "current": candidate})
        self.assertEqual(validate_profiles.profiles_create_next(evidence)["effect"], "discard-journal")

    def test_jcs_rejects_non_finite_numbers(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid_jcs_number"):
                validate_profiles.canonical_json({"future": value})

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

    def reset_lock(self, scenario, request, mutations=()):
        contract = json.loads(validate_profiles.VECTORS.read_text())["resetLockContract"]
        world = copy.deepcopy(contract["worldBase"])
        for key, override in contract["scenarios"][scenario].items():
            world["libraries"][key].update(copy.deepcopy(override))
        for mutation in mutations:
            validate_profiles.mutate_pointer(world, mutation)
        return validate_profiles.reset_lock_step(world, request)

    def reset_lock_request(self, name):
        contract = json.loads(validate_profiles.VECTORS.read_text())["resetLockContract"]
        return next(case for case in contract["cases"] if case["name"].startswith(name))

    def test_reset_lock_leaves_the_planner_alone(self):
        vectors = json.loads(validate_profiles.VECTORS.read_text())
        self.assertIsNone(vectors["resetLockContract"]["plannerChange"])
        self.assertEqual(len(vectors["resetCases"]), 13)

    def test_reset_lock_every_case_matches_the_model(self):
        contract = json.loads(validate_profiles.VECTORS.read_text())["resetLockContract"]
        for case in contract["cases"]:
            if "error" in case:
                with self.assertRaisesRegex(ValueError, case["error"]):
                    self.reset_lock(case["scenario"], case["request"], case.get("mutations", ()))
            else:
                self.assertEqual(
                    self.reset_lock(case["scenario"], case["request"], case.get("mutations", ())),
                    case["expect"], case["name"])

    def test_reset_lock_nothing_aborts_after_the_successor_commit(self):
        for name in ("the household cannot abort after the successor commit",
                     "the household cannot abort once retirement",
                     "recovery after the commit can never abort"):
            case = self.reset_lock_request(name)
            actual = self.reset_lock(case["scenario"], case["request"])
            self.assertEqual((actual["status"], actual["error"]), (409, "migration_in_progress"))
            self.assertEqual(actual["sideEffects"], [])

    def test_reset_lock_household_is_released_and_retired_last(self):
        for name in ("the household cannot retire while", "the household cannot retire before the door"):
            retired = self.reset_lock_request(name)
            self.assertEqual(self.reset_lock(retired["scenario"], retired["request"])["error"], "locks_held")

    def test_reset_lock_commit_is_never_inferred_from_an_existing_successor(self):
        # A successor library with rows but no recorded commit does not make the reset committed.
        case = self.reset_lock_request("no library retires before the successor commit")
        actual = self.reset_lock("set-ready", case["request"])
        self.assertEqual(actual["error"], "successor_not_committed")

    def test_reset_lock_takeover_decides_nothing_by_itself_and_shuts_out_the_old_owner(self):
        take = self.reset_lock_request("recovery takes over an open reset")
        self.assertEqual(self.reset_lock(take["scenario"], take["request"])["effect"], "takeover")
        for name in ("a stale owner is shut out of the commit", "the taken-over owner is shut out",
                     "the taken-over owner cannot retire"):
            case = self.reset_lock_request(name)
            self.assertEqual(self.reset_lock(case["scenario"], case["request"])["error"],
                             "migration_in_progress")

    def test_reset_lock_recovery_after_the_commit_needs_proof_or_rolls_back_untouched(self):
        def run(name):
            case = self.reset_lock_request(name)
            return self.reset_lock(case["scenario"], case["request"], case.get("mutations", ()))
        self.assertEqual(run("recovery with proof of the successor key")["effect"], "takeover")
        rollback = run("recovery without proof after the commit")
        self.assertEqual(rollback["effect"], "rollback")
        self.assertEqual(rollback["sideEffects"], ["household-aborted"])
        for name in ("recovery without proof once any library", "recovery without proof once every member",
                     "recovery without proof after the door",
                     "recovery with a wrong proof is refused after", "recovery with a wrong proof is refused before"):
            refused = run(name)
            self.assertEqual((refused["status"], refused["error"], refused["sideEffects"]),
                             (409, "migration_in_progress", []))

    def test_reset_lock_every_decision_is_one_household_commit(self):
        for name in ("abort is one commit on the household", "recovery without proof after the commit"):
            case = self.reset_lock_request(name)
            actual = self.reset_lock(case["scenario"], case["request"])
            self.assertEqual(actual["sideEffects"], ["household-aborted"])

    def test_reset_lock_void_member_locks_never_block_writes_or_retire(self):
        write = self.reset_lock_request("member writes are allowed at once")
        self.assertEqual(self.reset_lock(write["scenario"], write["request"])["effect"], "allowed")
        retire = self.reset_lock_request("after a rollback the old owner's member retirement")
        self.assertEqual(self.reset_lock(retire["scenario"], retire["request"])["error"], "lock_not_held")

    def test_reset_lock_successors_are_swept_after_the_decision_only(self):
        sweep = self.reset_lock_request("den-edge sweeps every declared successor")
        self.assertEqual(len(self.reset_lock(sweep["scenario"], sweep["request"])["delete"]), 3)
        for name in ("a live or committed reset is never swept", "a retiring reset is never swept"):
            case = self.reset_lock_request(name)
            self.assertEqual(self.reset_lock(case["scenario"], case["request"])["effect"], "none")

    def test_reset_lock_sweep_never_deletes_a_successor_that_preexisted(self):
        case = self.reset_lock_request("a household cannot declare an existing library")
        actual = self.reset_lock(case["scenario"], case["request"])
        self.assertNotIn(case["expect"]["delete"][0], [])
        self.assertEqual(len(actual["delete"]), 2)
        acquire = self.reset_lock_request("a declared successor that already exists")
        self.assertEqual(len(self.reset_lock(acquire["scenario"], acquire["request"],
                                             acquire["mutations"])["preexisting"]), 1)

    def test_reset_lock_replays_and_renderings(self):
        for name in ("a repeated rollback request", "the recovery owner repeats a member retirement"):
            case = self.reset_lock_request(name)
            self.assertEqual(self.reset_lock(case["scenario"], case["request"])["effect"], "lookup")
        gone = self.reset_lock_request("an unrelated attempt gets library_moved")
        self.assertEqual(self.reset_lock(gone["scenario"], gone["request"])["status"], 410)
        door = self.reset_lock_request("lookup renders retiring as committed")
        self.assertEqual(self.reset_lock(door["scenario"], door["request"])["state"], "committed")
        locked = self.reset_lock_request("a migration-locked library answers exclusive headers")
        self.assertEqual(self.reset_lock(locked["scenario"], locked["request"])["error"],
                         "migration_in_progress")
        for name in ("a malformed attempt header", "a malformed attempt in the abort", "a malformed attempt in the commit"):
            case = self.reset_lock_request(name)
            with self.assertRaisesRegex(ValueError, "bad_request"):
                self.reset_lock(case["scenario"], case["request"])


if __name__ == "__main__":
    unittest.main()
