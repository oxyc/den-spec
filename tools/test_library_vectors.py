import copy
import json
import tempfile
import unittest
from pathlib import Path

import prove_library_v4_reverse_contract
import validate_library_vectors


class LibraryVectorsTests(unittest.TestCase):
    def test_library_v4_vectors(self):
        self.assertEqual(validate_library_vectors.main(), 0)

    def test_malformed_watchlist_vector_fails(self):
        vectors = json.loads(validate_library_vectors.VECTORS.read_text())
        malformed = copy.deepcopy(vectors)
        case = next(
            case
            for case in malformed["cases"]
            if isinstance(case["request"].get("write"), dict)
            and case["request"]["write"].get("kind") == "watchlist_remove"
        )
        case["request"]["write"]["at"] = [3000]
        with self.assertRaisesRegex(ValueError, "watchlist_remove"):
            validate_library_vectors.validate(malformed)

    def test_wrong_watchlist_outcome_passes_structural_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library-v4.json"
            path.write_bytes(validate_library_vectors.VECTORS.read_bytes())
            prove_library_v4_reverse_contract.install_wrong_outcome(path)
            vectors = json.loads(path.read_text())
            validate_library_vectors.validate(vectors)
            case = next(
                case
                for case in vectors["cases"]
                if case["name"] == prove_library_v4_reverse_contract.CASE
            )
            self.assertEqual(
                case["ok"]["documents"][0]["status"]["value"], "watched"
            )


if __name__ == "__main__":
    unittest.main()
