import unittest

from server import (
    ACCESS_TOKENS,
    build_download_payload,
    get_preview_limit_bytes,
    issue_access_token,
    resolve_audio_path,
    resolve_ebook_path,
)


class AudioAccessTests(unittest.TestCase):
    def test_resolve_audio_path_for_existing_chapter(self):
        path = resolve_audio_path("red-rising-audio", "CHAPTER 01.mp3")
        self.assertTrue(path is not None)
        self.assertTrue(path.exists())

    def test_issue_access_token_is_recorded(self):
        token = issue_access_token("red-rising-audio")
        self.assertIn(token, ACCESS_TOKENS)
        self.assertEqual(ACCESS_TOKENS[token], "red-rising-audio")

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


if __name__ == "__main__":
    unittest.main()
