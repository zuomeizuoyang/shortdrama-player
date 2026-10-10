"""Deterministic checks of cursor and media boundaries, with networking disabled."""
import unittest
import base64
import json
import zlib
from unittest.mock import patch

from .common import OfficialWebBase, MediaVerifierMixin, decode_cursor, encode_cursor, next_data


class Fixture(OfficialWebBase, MediaVerifierMixin):
    PLATFORM = "fixture"
    BASE_URL = "https://example.invalid"


class Response:
    status_code = 200
    headers = {"Content-Type": "application/x-mpegurl"}

    def __init__(self, text):
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        pass


class CommonControls(unittest.TestCase):
    def setUp(self):
        self.guard = patch("requests.sessions.Session.request", side_effect=AssertionError("Network forbidden"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_snapshot_has_unique_stable_pages_and_end(self):
        client = Fixture()
        page = client.snapshot_page([{"drama_id": str(i)} for i in (1, 2, 2, 3, 4, 5)], page_size=2)
        ids = [row["drama_id"] for row in page["items"]]
        while page["next_cursor"]:
            page = client.snapshot_page(cursor=page["next_cursor"], page_size=2)
            ids.extend(row["drama_id"] for row in page["items"])
        self.assertEqual(ids, ["1", "2", "3", "4", "5"])
        self.assertFalse(page["has_more"])

    def test_snapshot_rejects_cross_instance_and_language(self):
        client = Fixture()
        page = client.snapshot_page([{"drama_id": str(i)} for i in range(3)], page_size=1)
        with self.assertRaises(ValueError):
            Fixture().snapshot_page(cursor=page["next_cursor"], page_size=1)
        with self.assertRaises(ValueError):
            client.snapshot_page(cursor=page["next_cursor"], language="es", page_size=1)

    def test_invalid_cursor_rejected(self):
        with self.assertRaises(ValueError):
            decode_cursor("not_a_cursor", platform="fixture", kind="feed", language="en")

    def test_large_catalog_cursor_preserves_all_ids_and_language(self):
        state = {"platform": "fixture", "kind": "feed", "language": "en",
                 "seen_ids": [str(31000000000 + i) for i in range(4000)]}
        cursor = encode_cursor(state)
        self.assertLess(len(cursor), 32768)
        self.assertEqual(decode_cursor(cursor, platform="fixture", kind="feed", language="en"), state)
        with self.assertRaises(ValueError):
            decode_cursor(cursor, platform="fixture", kind="feed", language="es")

    def test_legacy_cursor_remains_valid(self):
        state = {"platform": "fixture", "kind": "feed", "language": "en", "page": 2}
        legacy = base64.urlsafe_b64encode(json.dumps(state).encode()).decode().rstrip("=")
        self.assertEqual(decode_cursor(legacy, platform="fixture", kind="feed", language="en"), state)

    def test_oversized_compressed_cursor_is_rejected(self):
        raw = json.dumps({"platform": "fixture", "kind": "feed", "language": "en",
                          "payload": "x" * 1_048_577}).encode()
        cursor = "z1:" + base64.urlsafe_b64encode(zlib.compress(raw)).decode().rstrip("=")
        with self.assertRaises(ValueError):
            decode_cursor(cursor, platform="fixture", kind="feed", language="en")

    def test_locked_does_not_fetch_media(self):
        self.assertFalse(Fixture().verify_stream({"status": "locked"})["verified"])

    def test_encrypted_manifest_does_not_fetch_key_or_segment(self):
        response = Response('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\nmedia.ts')
        with patch("overseas.common.requests.get", return_value=response) as request:
            result = Fixture().verify_stream({"status": "playable", "format": "hls", "url": "https://example.invalid/0.m3u8"})
            self.assertFalse(result["verified"])
            self.assertEqual(request.call_count, 1)

    def test_hls_cycle_is_bounded(self):
        response = Response('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n0.m3u8')
        with patch("overseas.common.requests.get", return_value=response) as request:
            result = Fixture().verify_stream({"status": "playable", "format": "hls", "url": "https://example.invalid/0.m3u8"})
            self.assertFalse(result["verified"])
            self.assertEqual(request.call_count, 1)

    def test_next_data_script_attribute_order(self):
        result = next_data('<script type="application/json" id="__NEXT_DATA__">{"page":"/"}</script>')
        self.assertEqual(result["page"], "/")


if __name__ == "__main__":
    unittest.main()
