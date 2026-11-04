import copy
import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
