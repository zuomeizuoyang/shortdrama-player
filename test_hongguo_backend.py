import json
from unittest import TestCase
from unittest.mock import patch, Mock
import threading

import requests

from hongguo_backend import PublicHongguoClient, PublicBackendError, router_data, build_backend
from hongguo import HongguoAdapter


SID = "123456789"
VIDS = ["101", "102", "103", "104"]


def detail_page(accessible=3):
    return {"detail_page": {"isSuccess": True, "seriesDetail": {
        "series_id": SID, "series_name": "测试短剧", "episode_cnt": 4,
        "vid_list": VIDS[:], "accessible_episode_cnt": accessible, "tags": ["爱情"]}}}


class PublicClientTests(TestCase):
    def setUp(self):
        self.client = PublicHongguoClient()
        self.addCleanup(self.client.close)

    def test_router_data_parses_json_without_executing_javascript(self):
        data = {"loaderData": detail_page(), "errors": None}
        source = '<script>window._ROUTER_DATA = ' + json.dumps(data) + ';throw Error("must not run")</script>'
        self.assertEqual(router_data(source), data["loaderData"])
        with self.assertRaises(PublicBackendError):
            router_data('<script>window._ROUTER_DATA = alert("invalid")</script>')

    def test_complete_chapters_and_official_web_access_boundary(self):
        with patch.object(self.client, "page", return_value=detail_page()):
            value = self.client.detail(SID)
        self.assertEqual(len(value["episodes"]), 4)
        self.assertEqual(value["meta"]["web_accessible_episode_count"], 3)
        self.assertFalse(value["episodes"][2]["official_only"])
        self.assertTrue(value["episodes"][3]["official_only"])
        self.assertEqual(value["episodes"][3]["access"], "official_app_required")

    def test_app_only_episode_never_requests_player_page(self):
        with patch.object(self.client, "page", return_value=detail_page()) as get:
            result = self.client.resolve(SID, 4)
        self.assertEqual(result["status"], "official_player_required")
        self.assertNotIn("url", result)
        self.assertEqual(get.call_count, 1)

    def test_missing_access_count_does_not_assume_three_free_episodes(self):
        page = detail_page()
        page["detail_page"]["seriesDetail"].pop("accessible_episode_cnt")
        with patch.object(self.client, "page", return_value=page) as get:
            result = self.client.resolve(SID, 1)
        self.assertEqual(result["status"], "official_player_required")
        self.assertEqual(get.call_count, 1)

    def test_incomplete_or_duplicate_vid_list_rejected(self):
        for vids in [VIDS[:2], ["101", "101", "103", "104"]]:
            page = detail_page()
            page["detail_page"]["seriesDetail"]["vid_list"] = vids
            with patch.object(self.client, "page", return_value=page), self.assertRaises(PublicBackendError):
                self.client.detail(SID)

    def test_public_player_must_return_this_episode(self):
        player = {"player_(series_id)/(vid)/page": {"series_id": SID, "vid": "101",
                   "video_player_info": {"main_url": "https://v3-hgweb.qznovelvod.com/public.mp4", "duration": 12}}}
        with patch.object(self.client, "page", side_effect=[detail_page(), player]):
            result = self.client.resolve(SID, 1)
        self.assertEqual(result["status"], "playable")
        self.assertEqual(result["format"], "mp4")
        self.client.cache.clear()
        player["player_(series_id)/(vid)/page"]["vid"] = "102"
        with patch.object(self.client, "page", side_effect=[detail_page(), player]), self.assertRaises(PublicBackendError):
            self.client.resolve(SID, 1)

    def test_protected_or_foreign_stream_not_returned(self):
        for info in [{"main_url": "https://v3-hgweb.qznovelvod.com/a.mp4", "encrypt_info": {}},
                     {"main_url": "https://example.org/a.mp4"},
                     {"main_url": "https://v3-hgweb.qznovelvod.com.evil.org/a.mp4"}]:
            self.client.cache.clear()
            player = {"player_(series_id)/(vid)/page": {"series_id": SID, "vid": "101", "video_player_info": info}}
            with patch.object(self.client, "page", side_effect=[detail_page(), player]):
                result = self.client.resolve(SID, 1)
            self.assertEqual(result["status"], "official_player_required")
            self.assertNotIn("url", result)

    def test_search_uses_returned_keyword_and_ignores_actor_results(self):
        page = {"search_(keyword)/page": {"query": "爱情", "isSuccess": True, "totalCount": "30",
                    "searchList": [{"user_info": {"name": "演员"}},
                                   {"video_data": {"series_id": SID, "series_title": "测试短剧", "episode_cnt": 4}}]}}
        with patch.object(self.client, "page", return_value=page) as get:
            value = self.client.search("爱情")
        self.assertEqual(get.call_args.args[0], "/search/%E7%88%B1%E6%83%85")
        self.assertEqual(len(value["results"]), 1)
        self.assertEqual(value["total"], 30)
        self.assertEqual(value["pagination"], "official_web_first_page")
        self.client.cache.clear()
        page["search_(keyword)/page"]["query"] = ""
        with patch.object(self.client, "page", return_value=page), self.assertRaises(PublicBackendError):
            self.client.search("爱情")

    def test_bridge_preserves_official_only_state_and_detail_count(self):
        client = HongguoAdapter("http://127.0.0.1:8000")
        self.addCleanup(client.close)
        value = {"meta": {"series_id": SID, "title": "测试", "episode_cnt": 1, "web_accessible_episode_count": 0},
                 "episodes": [{"index": 1, "vid": "101", "locked": False, "official_only": True,
                               "access": "official_app_required", "official_url": "https://hongguoduanju.com/"}]}
        with patch.object(client, "_api", return_value=value) as get:
            result = client.resolvePlayback(f"hongguo:{SID}:1")
            self.assertEqual(client.detail(SID)["web_accessible_episode_count"], 0)
        self.assertEqual(result["status"], "official_player_required")
        self.assertIsNone(result["url"])
        self.assertEqual(get.call_count, 1)

    def test_search_empty_first_page_with_nonzero_total_is_retried_not_cached(self):
        empty = {"search_(keyword)/page": {"query": "爱情", "isSuccess": True,
                   "totalCount": "30", "searchList": []}}
        full = {"search_(keyword)/page": {"query": "爱情", "isSuccess": True,
                   "totalCount": "30", "searchList": [{"video_data": {"series_id": SID}}]}}
        with patch.object(self.client, "page", side_effect=[empty, full]) as get:
            self.assertEqual(len(self.client.search("爱情")["results"]), 1)
            self.assertEqual(get.call_count, 2)
        self.client.cache.clear()
        with patch.object(self.client, "page", return_value=empty) as get, self.assertRaises(PublicBackendError):
            self.client.search("爱情")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(self.client.cache, {})


class BackendHttpTests(TestCase):
    def setUp(self):
        self.server = build_backend(port=0, api_key="fixture-key-not-a-credential")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.headers = {"X-API-Key": self.server.state.api_key}
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.state.client.close()

    def test_health_does_not_claim_all_episodes_playable_and_key_required(self):
        response = requests.get(self.base + "/health", timeout=3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["playback_scope"], "official_web_accessible_episodes")
        self.assertNotIn(self.server.state.api_key, response.text)
        response = requests.get(self.base + "/browse", timeout=3)
        self.assertEqual(response.status_code, 401)

    def test_play_returns_opaque_stream_not_cdn_url(self):
        row = {"index": 1, "vid": "101", "status": "playable", "format": "mp4",
               "url": "https://v3-hgweb.qznovelvod.com/fixture.mp4"}
        with patch.object(self.server.state.client, "resolve", return_value=row):
            response = requests.get(self.base + "/play?series_id=" + SID + "&ep=1", headers=self.headers, timeout=3)
        self.assertEqual(response.status_code, 200)
        result = response.json()["episodes"][0]
        self.assertTrue(result["stream_url"].startswith("/stream?token="))
        self.assertNotIn("url", result)
        self.assertNotIn("fixture.mp4", response.text)

    def test_invalid_or_expired_stream_token_rejected(self):
        response = requests.get(self.base + "/stream?token=not-a-token", headers=self.headers, timeout=3)
        self.assertEqual(response.status_code, 400)

    def test_stream_forwards_range_and_never_local_key(self):
        token_url = self.server.state.stream_token("https://v3-hgweb.qznovelvod.com/fixture.mp4")
        upstream = Mock()
        upstream.__enter__ = Mock(return_value=upstream)
        upstream.__exit__ = Mock(return_value=False)
        upstream.status_code = 206
        upstream.headers = {"Content-Type": "video/mp4", "Content-Length": "4", "Content-Range": "bytes 0-3/100"}
        upstream.iter_content.return_value = [b"ftyp"]
        # Patch only the backend's CDN call; perform the localhost HTTP read
        # through Session.request so the test still traverses the real handler.
        with patch("hongguo_backend.requests.get", return_value=upstream) as get:
            response = requests.Session().get(self.base + token_url, headers={**self.headers, "Range": "bytes=0-3"}, timeout=3)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"ftyp")
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=0-3")
        self.assertNotIn("X-API-Key", get.call_args.kwargs["headers"])
