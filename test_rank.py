"""Rank request mapping, authenticated HTTP contract and aggregate ordering."""
from unittest import TestCase
from unittest.mock import Mock, patch
import threading

import requests

from hongguo_backend import PublicHongguoClient, build_backend
from providers import ProviderManager


class RankTests(TestCase):
    def test_series_and_comic_route_to_distinct_official_boards(self):
        client = PublicHongguoClient()
        self.addCleanup(client.close)
        response = Mock()
        response.json.return_value = {"code": 0, "data": {"cell_view": {"cell_data": []}}}
        with patch.object(client.session, "get", return_value=response) as get:
            client.rank(category="series", board="new_rank")
            self.assertEqual(get.call_args.kwargs["params"]["sub_selected_items"], "new_rank")
            client.rank(category="comic_series_rank", board="new_rank")
            self.assertEqual(get.call_args.kwargs["params"]["sub_selected_items"], "comic_series_new_rank")

    def test_ai_rank_retains_pagination_and_selected_sort(self):
        client = PublicHongguoClient()
        self.addCleanup(client.close)
        response = Mock()
        response.json.return_value = {"code": 0, "data": {"has_more": True, "next_offset": 21,
            "video_data": [{"series_id": "123", "title": "AI story", "episode_cnt": 4}]}}
        with patch.object(client.session, "post", return_value=response) as post:
            page = client.rank(category="ai_series", board="hot_play", offset=20)
        self.assertEqual(post.call_args.kwargs["json"]["select_items"]["sort"], ["hot_score"])
        self.assertEqual(page["next_offset"], 21)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["items"][0]["category"], ["AI剧"])

    def test_rank_http_requires_key_and_forwards_category_board_offset(self):
        server = build_backend(port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/rank"
            self.assertEqual(requests.get(url, timeout=3).status_code, 401)
            with patch.object(server.state.client, "rank", return_value={"items": []}) as rank:
                response = requests.get(url, params={"category": "ai_series", "board": "hot_play", "offset": 20},
                    headers={"X-API-Key": server.state.api_key}, timeout=3)
            self.assertEqual(response.status_code, 200)
            rank.assert_called_once_with(category="ai_series", board="hot_play", offset=20, limit=20)
        finally:
            server.shutdown()
            server.server_close()
            server.state.client.close()

    def test_aggregate_interleaves_platforms_and_limits_each_to_five(self):
        manager = ProviderManager()
        self.addCleanup(manager.close)
        def client(session, platform, language):
            adapter = Mock()
            adapter.rank.return_value = {"items": [{"drama_id": str(i), "title": platform} for i in range(7)]}
            return adapter
        with patch.object(manager, "client", side_effect=client):
            page = manager.rank_all(Mock())
        self.assertEqual(len(page["items"]), 30)
        self.assertEqual([row["rank"] for row in page["items"]], list(range(1, 31)))
        self.assertEqual([row["platform"] for row in page["items"][:6]],
            ["hongguo", "dramabox", "reelshort", "dramawave", "netshort", "goodshort"])
        self.assertTrue(all(row["drama_id"] == "0" for row in page["items"][:6]))

    def test_aggregate_keeps_other_platforms_when_one_fails(self):
        manager = ProviderManager()
        self.addCleanup(manager.close)
        def client(session, platform, language):
            if platform == "hongguo":
                raise RuntimeError("fixture failure")
            adapter = Mock()
            adapter.rank.return_value = {"items": [{"drama_id": "1", "title": platform}]}
            return adapter
        with patch.object(manager, "client", side_effect=client):
            page = manager.rank_all(Mock())
        self.assertEqual(len(page["items"]), 5)
        self.assertNotIn("hongguo", {row["platform"] for row in page["items"]})
