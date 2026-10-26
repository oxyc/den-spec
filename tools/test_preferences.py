import unittest

import validate_preferences


class PreferencesVectorsTests(unittest.TestCase):
    def test_preferences_v1_vectors(self):
        self.assertEqual(validate_preferences.main(), 0)

    def test_presence_distinguishes_fresh_from_stale_web_off(self):
        self.assertEqual(validate_preferences.read({}), {"mode": "automatic"})
        self.assertEqual(
            validate_preferences.read({"den.preferredSubtitleLanguage": None}),
            {"mode": "off"},
        )


if __name__ == "__main__":
    unittest.main()
