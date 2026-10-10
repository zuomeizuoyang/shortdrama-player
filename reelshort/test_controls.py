"""Offline access and HLS safety controls: python -m unittest reelshort.test_controls."""
import json
import unittest
from unittest.mock import patch

from .playback import PlaybackMixin


class MockClient(PlaybackMixin):
    def __init__(self, data=None, flag=None):
        self.data = data or {}
        self.flag = flag
        self.html_reads = 0

    def episodes(self, *args, **kwargs):
        return [{"chapter_id": "c", "number": 1, "is_lock": self.flag}]

    def get_html(self, *args, **kwargs):
        self.html_reads += 1
        return '<script id="__NEXT_DATA__">' + json.dumps(
            {"props": {"pageProps": {"data": self.data}}}) + '</script>'


class ManifestResponse:
    status_code = 200
    headers = {"Content-Type": "application/x-mpegurl"}

    def __init__(self, text):
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        pass


class PlaybackControls(unittest.TestCase):
    def setUp(self):
        # Any accidental real networking is a test failure.
        self.no_network = patch("requests.sessions.Session.request",
                                side_effect=AssertionError("Offline tests must not access the network"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.raw = {"book_id": "d", "chapter_id": "c", "serial_number": 1,
                    "video_url": "https://cdn.example/0.m3u8"}
        self.play = {"status": "playable", "format": "hls",
                     "url": "https://cdn.example/0.m3u8"}

    def check_access(self, extra, flag, status, reason=None):
        result = MockClient({**self.raw, **extra}, flag).resolvePlayback("d:c")
        self.assertEqual(result["status"], status)
        self.assertIsNone(result["expires_at"])
        if reason:
            self.assertEqual(result["reason"], reason)
        if status != "playable":
            self.assertIsNone(result["url"])

    def test_unknown_access_missing_boundary(self):
        self.check_access({}, None, "official_page", "access_unconfirmed")

    def test_unknown_access_zero_boundary(self):
        self.check_access({"paid_start": 0}, None, "official_page", "access_unconfirmed")

    def test_unknown_access_below_free_boundary(self):
        self.check_access({"paid_start": 10}, None, "playable")

    def test_unknown_access_explicitly_free_drama(self):
        self.check_access({"is_paid": 0}, None, "playable")

    def test_api_free_access_without_boundary(self):
        self.check_access({}, 0, "playable")

    def test_api_purchased_access_without_boundary(self):
        self.check_access({}, 2, "playable")

    def test_unknown_access_paid_boundary(self):
        self.check_access({"serial_number": 10, "paid_start": 10}, None, "locked")

    def test_unknown_access_vip_requires_entitlement(self):
        self.check_access({"paid_start": 10, "vip_free": 1}, None, "locked")

    def test_locked_api_chapter_never_reads_page_or_stream(self):
        client = MockClient(self.raw, 1)
        result = client.resolvePlayback("d:c")
        self.assertEqual(result["status"], "locked")
        self.assertIsNone(result["url"])
        self.assertEqual(client.html_reads, 0)
        self.assertFalse(client.verify_stream(result)["verified"])

    def test_cyclic_master_manifest_stops_after_one_request(self):
        manifest = ManifestResponse("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n0.m3u8")
        with patch("reelshort.playback.requests.get", return_value=manifest) as request:
            result = MockClient().verify_stream(self.play)
            self.assertEqual(result["reason"], "hls_manifest_cycle")
            self.assertEqual(request.call_count, 1)

    def test_master_manifest_depth_limited_to_three_requests(self):
        manifests = [ManifestResponse("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n"
                                      + str(index) + ".m3u8") for index in (1, 2, 3)]
        with patch("reelshort.playback.requests.get", side_effect=manifests) as request:
            result = MockClient().verify_stream(self.play)
            self.assertEqual(result["reason"], "hls_manifest_depth_limit")
            self.assertEqual(request.call_count, 3)

    def test_encrypted_manifest_never_fetches_key_or_segment(self):
        manifest = ManifestResponse('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\nsegment.ts')
        with patch("reelshort.playback.requests.get", return_value=manifest) as request:
            result = MockClient().verify_stream(self.play)
            self.assertEqual(result["reason"], "encrypted_media_official_player_required")
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], self.play["url"])


if __name__ == "__main__":
    unittest.main()
