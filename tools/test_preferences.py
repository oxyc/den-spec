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

    def test_interface_language_distinguishes_inheritance_from_explicit_english(self):
        self.assertEqual(
            validate_preferences.read_interface_language({}),
            {"language": "en", "explicit": False, "supported": True},
        )
        self.assertEqual(
            validate_preferences.read_interface_language(
                {"den.interfaceLanguage": {"string": "en"}}
            ),
            {"language": "en", "explicit": True, "supported": True},
        )

    def test_future_interface_language_is_preserved(self):
        self.assertEqual(
            validate_preferences.read_interface_language(
                {"den.interfaceLanguage": {"string": "fr"}}
            ),
            {
                "language": "en",
                "explicit": True,
                "supported": False,
                "storedLanguage": "fr",
            },
        )


if __name__ == "__main__":
    unittest.main()
