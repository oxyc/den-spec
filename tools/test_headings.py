import unittest

import validate_headings


class HeadingVectorsTests(unittest.TestCase):
    def test_heading_v1_vectors(self):
        self.assertEqual(validate_headings.main(), 0)

    def test_legacy_copy_is_never_classified_from_english(self):
        self.assertEqual(
            validate_headings.normalize("Because you watched Dune"),
            {"id": "den.legacy", "args": {}, "fallback": "Because you watched Dune"},
        )

    def test_external_copy_that_matches_product_copy_stays_verbatim(self):
        heading = {"id": "den.verbatim", "args": {"value": "Watchlist"}, "fallback": "Watchlist"}
        self.assertEqual(validate_headings.normalize(heading), heading)


if __name__ == "__main__":
    unittest.main()
