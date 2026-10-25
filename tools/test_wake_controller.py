import json
import subprocess
import unittest

import validate_wake_controller


class WakeControllerVectorsTests(unittest.TestCase):
    def test_vectors_validate(self):
        validate_wake_controller.validate()

    def test_generator_matches_committed_vectors(self):
        generated = subprocess.run(
            ["node", "tools/wake-controller-vectors.mjs"],
            cwd=validate_wake_controller.ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(json.loads(generated), json.loads(validate_wake_controller.VECTORS.read_text()))

    def test_noncanonical_credentials_are_refused(self):
        credential = "a" * 48
        expected = validate_wake_controller.wake_inbox(credential)
        self.assertRegex(expected, r"^[0-9a-f]{64}$")
        for invalid in (credential.upper(), credential[:-1], credential + "0", f" {credential}"):
            with self.assertRaisesRegex(ValueError, "invalid_inbox_credential"):
                validate_wake_controller.wake_inbox(invalid)

    def test_hkdf_is_rfc5869(self):
        # RFC 5869 test case 1.
        output = validate_wake_controller.hkdf_sha256(
            bytes.fromhex("0b" * 22),
            bytes.fromhex("000102030405060708090a0b0c"),
            bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
            42,
        )
        self.assertEqual(
            output.hex(),
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865",
        )


if __name__ == "__main__":
    unittest.main()

