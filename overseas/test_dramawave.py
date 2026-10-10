"""Offline tests: python -m unittest overseas.test_dramawave."""
import unittest
from unittest.mock import patch

from .common import AdapterError, encode_cursor
from .dramawave import DramaWaveAdapter


class DramaWaveControls(unittest.TestCase):
    def setUp(self):
        no_network = patch("requests.sessions.Session.request",
                           side_effect=AssertionError("Tests must not access the network"))
        no_network.start()
        self.addCleanup(no_network.stop)
        self.adapter = DramaWaveAdapter()
        self.episode = {"id": "e1", "index": 1, "name": "Sample", "unlock": True,
                        "video_type": "free", "h5_available": True,
                        "external_audio_h264_m3u8": "https://video-v6.mydramawave.com/sample.m3u8"}

    def resolve(self, **changes):
        raw = {"id": "s1", "name": "Sample", "episode_list": [{**self.episode, **changes}]}
        with patch.object(self.adapter, "_raw_detail", return_value=raw):
            return self.adapter.resolvePlayback("s1:e1")

    def test_free_authorized_web_episode(self):
        result = self.resolve()
        self.assertEqual(result["status"], "playable")
        self.assertEqual(result["format"], "hls")
        self.assertIsNone(result["expires_at"])

    def test_locked_url_is_not_exposed_even_if_present(self):
        result = self.resolve(unlock=False, video_type="charge")
        self.assertEqual(result["status"], "locked")
        self.assertIsNone(result["url"])
        self.assertFalse(self.adapter.verify_stream(result)["verified"])

    def test_unknown_access_with_url_does_not_play(self):
        result = self.resolve(unlock=None)
        self.assertEqual(result["reason"], "access_unconfirmed")
        self.assertIsNone(result["url"])

    def test_app_only_episode_returns_official_page(self):
        result = self.resolve(h5_available=False)
        self.assertEqual(result["status"], "official_page")
        self.assertEqual(result["reason"], "official_app_required")

    def test_current_entitlement_allows_charge_episode(self):
        self.assertEqual(self.resolve(video_type="charge")["status"], "playable")

    def test_empty_authorized_stream_returns_official_page(self):
        result = self.resolve(external_audio_h264_m3u8="")
        self.assertEqual(result["reason"], "no_authorized_stream")

    def test_unknown_episode_returns_not_found(self):
        with patch.object(self.adapter, "_raw_detail", return_value={"episode_list": []}):
            self.assertEqual(self.adapter.resolvePlayback("s1:e1")["status"], "not_found")

    def test_auth_denied_returns_login_required(self):
        with patch.object(self.adapter, "_raw_detail", side_effect=AdapterError("Denied", code=401)):
            self.assertEqual(self.adapter.resolvePlayback("s1:e1")["status"], "login_required")

    def test_unlock_and_sister_platform_routes_are_rejected(self):
        for path in ("/h5-api/drama/unlock_episode", "/frv2-api/drama/info"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.adapter._request(path, method="POST")

    def test_feed_deduplicates_and_cursor_keeps_official_module(self):
        responses = [{"list": [{"tab_key": "28", "position_index": 10000}]},
                     {"items": [{"type": "banner", "items": [{"key": "s1", "title": "A"}]},
                                {"type": "recommend", "module_key": "41",
                                 "items": [{"key": "s1", "title": "A"}, {"key": "s2", "title": "B"}]}],
                      "page_info": {"next": "offset=10", "has_more": True}},
                     {"items": [{"key": "s2", "title": "B"}, {"key": "s3", "title": "C"}],
                      "page_info": {"next": "", "has_more": False}}]
        with patch.object(self.adapter, "_request", side_effect=responses) as request:
            first = self.adapter.feed()
            second = self.adapter.feed(first["next_cursor"])
        self.assertEqual([row["id"] for row in first["items"]], ["s1", "s2"])
        self.assertEqual([row["id"] for row in second["items"]], ["s3"])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(request.call_args.kwargs["data"], {"module_key": "41", "next": "offset=10"})

    def test_search_actual_empty_result_has_terminal_cursor(self):
        with patch.object(self.adapter, "_request", return_value={"page_info": {"has_more": False, "next": ""}}):
            result = self.adapter.search("No match")
        self.assertEqual(result["items"], [])
        self.assertFalse(result["has_more"])
        self.assertIsNone(result["next_cursor"])

    def test_search_cursor_cannot_change_keyword_or_platform(self):
        for platform, keyword in (("freereels", "CEO"), ("dramawave", "Love")):
            cursor = encode_cursor({"platform": platform, "kind": "search", "language": "en",
                                    "keyword": keyword, "next": "offset=20"})
            with self.subTest(platform=platform, keyword=keyword), self.assertRaises(ValueError):
                self.adapter.search("CEO", cursor)

    def test_repeating_server_cursor_is_rejected(self):
        with self.assertRaises(AdapterError):
            self.adapter._page([{"id": "s1"}], {"page_info": {"next": "offset=20", "has_more": True}},
                               language="en", kind="search", state={"next": "offset=20"}, source="test")

    def test_episode_metadata_excludes_streams(self):
        raw = {"id": "s1", "episode_list": [self.episode]}
        with patch.object(self.adapter, "_raw_detail", return_value=raw):
            episode = self.adapter.episodes("s1")[0]
        self.assertEqual(episode["episode_id"], "s1:e1")
        self.assertEqual(episode["access"], "free")
        self.assertNotIn("external_audio_h264_m3u8", episode)

    def test_main_count_preserved_with_two_bonus_entries(self):
        entries = [{**self.episode, "id": "e" + str(index), "index": index,
                    "is_blooper": index > 60} for index in range(1, 63)]
        raw = {"id": "s1", "name": "Sample", "episode_count": 60,
               "blooper_title": "Exclusive behind the scenes", "episode_list": entries}
        with patch.object(self.adapter, "_request", return_value={"info": raw}):
            detail = self.adapter.detail("s1")
            episodes = self.adapter.episodes("s1")
        self.assertEqual(detail["episode_count"], 60)
        self.assertEqual(detail["episode_count_returned"], 62)
        self.assertEqual(detail["bonus_episode_count"], 2)
        self.assertEqual(sum(not ep["is_blooper"] for ep in episodes), 60)
        self.assertTrue(episodes[-1]["is_blooper"])
        self.assertEqual(episodes[-1]["blooper_title"], raw["blooper_title"])
        self.assertIsNone(episodes[0]["blooper_title"])

    def test_missing_main_episode_rejected_despite_bonus_entries(self):
        raw = {"id": "s1", "episode_count": 2,
               "episode_list": [self.episode, {**self.episode, "id": "bonus", "is_blooper": True}]}
        with patch.object(self.adapter, "_request", return_value={"info": raw}):
            with self.assertRaisesRegex(AdapterError, "main episode list is incomplete"):
                self.adapter.detail("s1")
        self.assertNotIn(("s1", "en"), self.adapter._details)


if __name__ == "__main__":
    unittest.main()
