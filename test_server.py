import hashlib
import hmac
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server

from server import (
    build_download_payload,
    get_preview_limit_bytes,
    resolve_audio_path,
    resolve_ebook_path,
    valid_signature,
)
from server import RedRisingServerHandler
from commerce import CommerceStore


class AudioAccessTests(unittest.TestCase):
    def setUp(self):
        # Portable fixtures keep CI independent of paid media and live keys.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        audio = root / 'audio'
        audio.mkdir()
        (audio / 'CHAPTER 01.mp3').write_bytes(b'audio' * 100)
        ebook = root / 'book.epub'
        ebook.write_bytes(b'ebook' * 100)
        mocks = patch.multiple(server,
            AUDIO_PRODUCT_FOLDERS={'red-rising-audio': {'path': audio}},
            EBOOK_PRODUCT_FILES={'morning-star-ebook': [ebook]})
        mocks.start()
        self.addCleanup(mocks.stop)

    def test_resolve_audio_path_for_existing_chapter(self):
        path = resolve_audio_path("red-rising-audio", "CHAPTER 01.mp3")
        self.assertTrue(path is not None)
        self.assertTrue(path.exists())

    def test_preview_limit_bytes_is_positive(self):
        path = resolve_audio_path("red-rising-audio", "CHAPTER 01.mp3")
        limit = get_preview_limit_bytes(path, 600)
        self.assertGreater(limit, 0)

    def test_resolve_ebook_path_for_existing_ebook(self):
        path = resolve_ebook_path("morning-star-ebook")
        self.assertTrue(path is not None)
        self.assertTrue(path.exists())

    def test_build_download_payload_for_combo_contains_multiple_assets(self):
        payload = build_download_payload("saga-combo")
        self.assertTrue(payload["is_bundle"])
        self.assertGreaterEqual(len(payload["files"]), 2)

    def test_path_traversal_is_rejected(self):
        path = resolve_audio_path("red-rising-audio", "../secret.mp3")
        self.assertIsNone(path)


class RazorpayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.secret = "dummy_test_secret_for_hmac_unit_tests"
        self.order_id = "order_test_9876543210"
        self.payment_id = "pay_test_1234567890"

    def test_valid_hmac_sha256_signature_verification(self):
        msg = f"{self.order_id}|{self.payment_id}".encode("utf-8")
        expected_sig = hmac.new(self.secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature(self.secret, msg, expected_sig))

    def test_invalid_signature_is_rejected(self):
        msg = f"{self.order_id}|{self.payment_id}".encode("utf-8")
        self.assertFalse(valid_signature(self.secret, msg, "tampered_signature_value"))

    def test_empty_signature_is_rejected(self):
        msg = f"{self.order_id}|{self.payment_id}".encode("utf-8")
        self.assertFalse(valid_signature(self.secret, msg, ""))

    def test_malformed_signature_is_rejected_without_exception(self):
        for signature in ("é" * 64, "g" * 64, None, 123, [], "a" * 65):
            with self.subTest(signature=signature):
                self.assertFalse(valid_signature(self.secret, b"order|payment", signature))



    def test_catalog_prices_meet_minimum_amount(self):
        from server import CATALOG
        for product_id, (title, amounts) in CATALOG.items():
            for curr, amt in amounts.items():
                self.assertGreaterEqual(amt, 99, f"{product_id} price for {curr} is below 99")
                if curr == "INR":
                    self.assertGreaterEqual(amt, 100, f"{product_id} INR price is below 100 paise minimum")

    def test_signature_verification_algorithm_matches_razorpay_spec(self):
        # Algorithm: HMAC-SHA256(order_id + "|" + payment_id, KEY_SECRET)
        # Uses a dummy secret to verify the algorithm itself, not real credentials.
        secret = "dummy_hmac_verification_secret"
        order_id = "order_O8x7wB9k8e"
        payment_id = "pay_O8x8aL2k1p"
        msg = f"{order_id}|{payment_id}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature(secret, msg, expected))
        self.assertFalse(valid_signature(secret, msg, "fake_signature"))


class CommerceLockdownTests(unittest.TestCase):
    def post(self, path, payload):
        handler = object.__new__(RedRisingServerHandler)
        handler.path = path
        handler.read_json_body = lambda: payload
        captured = {}
        handler.send_error_response = lambda status, message: captured.update(status=status, message=message)
        handler.do_POST()
        return captured["status"], captured

    def test_entitlement_grant_endpoint_is_removed(self):
        status, _ = self.post("/api/access/grant", {"product_id": "red-rising-ebook"})
        self.assertEqual(status, 404)


class CommerceStoreTests(unittest.TestCase):
    def setUp(self):
        descriptor, self.path = tempfile.mkstemp()
        os.close(descriptor)
        self.store = CommerceStore(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_paid_order_creates_an_expiring_session_entitlement(self):
        self.store.create_order("order_test", "red-rising-audio", 19900, "INR")
        self.assertTrue(self.store.mark_payment_captured("order_test", "pay_test"))
        token, _ = self.store.issue_session_for_order("order_test")
        self.assertTrue(self.store.has_access(token, "red-rising-audio"))
        self.assertFalse(self.store.has_access(token, "golden-son-audio"))

    def test_combo_entitlement_grants_catalog_access(self):
        self.store.create_order("order_combo", "saga-combo", 89900, "INR")
        self.store.mark_payment_captured("order_combo", "pay_combo")
        token, _ = self.store.issue_session_for_order("order_combo")
        self.assertTrue(self.store.has_access(token, "light-bringer-ebook"))

if __name__ == "__main__":
    unittest.main()
