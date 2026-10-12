import unittest

import validate_profiles


class ProfilesVectorsTests(unittest.TestCase):
    def test_profiles_v1_vectors(self):
        self.assertEqual(validate_profiles.main(), 0)


if __name__ == "__main__":
    unittest.main()
