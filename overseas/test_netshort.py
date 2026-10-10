"""Offline guards for authorization, ID matching and public search cursors."""
import unittest
import time
from unittest.mock import Mock

from .netshort import NetShortAdapter
from .common import AdapterError


BOOK = "2103784897595645954"
CHAPTER = "2104477292846989314"


class NetShortTests(unittest.TestCase):
    def make_adapter(self, locked):
        adapter = NetShortAdapter()
        adapter._details[(BOOK, "en")] = (time.monotonic(), {
            "shortPlayId": BOOK, "shortPlayUrl": "/episode/example-" + BOOK,
            "videoEpisodeInfos": [{"episodeId": CHAPTER, "episodeNo": 1, "isLock": locked}]})
        adapter._api = Mock()
        self.addCleanup(adapter.close)
        return adapter

    def test_locked_episode_never_requests_media(self):
        adapter = self.make_adapter(True)
        result = adapter.resolvePlayback(BOOK + ":" + CHAPTER)
        self.assertEqual(result["status"], "locked")
        self.assertIsNone(result["url"])
        adapter._api.assert_not_called()

    def test_unknown_entitlement_never_requests_media(self):
        adapter = self.make_adapter(None)
        result = adapter.resolvePlayback(BOOK + ":" + CHAPTER)
        self.assertEqual(result["status"], "official_player_required")
        self.assertIsNone(result["url"])
        adapter._api.assert_not_called()

    def test_playback_rechecks_id_and_lock(self):
        adapter = self.make_adapter(False)
        adapter._api.return_value = {"episodeId": CHAPTER, "isLock": True,
                                      "playVoucher": "https://example.invalid/hidden.mp4"}
        result = adapter.resolvePlayback(BOOK + ":" + CHAPTER)
        self.assertIsNone(result["url"])
        self.assertEqual(result["status"], "locked")
        adapter._api.return_value = {"episodeId": "2104477292914089985", "isLock": False}
        with self.assertRaises(AdapterError):
            adapter.resolvePlayback(BOOK + ":" + CHAPTER)

    def test_search_extra_rows_repeat_dedup_and_terminal(self):
        adapter = NetShortAdapter()
        self.addCleanup(adapter.close)
        def response(path, body, language):
            page = body["pageQuery"]["pageNum"]
            ids = range(15) if page == 1 else range(10, 25) if page == 2 else range(10, 25)
            return {"list": [{"shortPlayId": str(BOOK[:-2]) + str(i).zfill(2),
                              "shortPlayName": "Test"} for i in ids], "totalHits": 10000}
        adapter._api = Mock(side_effect=response)
        first = adapter.search("love")
        second = adapter.search("love", first["next_cursor"])
        self.assertTrue(first["has_more"])
        self.assertEqual(len(second["items"]), 10)
        self.assertFalse({x["id"] for x in first["items"]} & {x["id"] for x in second["items"]})
        last = adapter.search("love", second["next_cursor"])
        self.assertFalse(last["has_more"])
        self.assertIsNone(last["next_cursor"])
        with self.assertRaises(ValueError):
            adapter.search("other", first["next_cursor"])

    def test_search_deduplicates_same_page(self):
        adapter = NetShortAdapter()
        self.addCleanup(adapter.close)
        row = {"shortPlayId": BOOK, "shortPlayName": "Test"}
        adapter._api = Mock(return_value={"list": [row, row], "totalHits": 10000})
        result = adapter.search("test")
        self.assertEqual(len(result["items"]), 1)
        self.assertFalse(result["has_more"])

    def test_declared_episode_count_cannot_hide_truncated_list(self):
        adapter = NetShortAdapter()
        self.addCleanup(adapter.close)
        adapter._api = Mock(return_value={"shortPlayId": BOOK, "totalEpisode": 51,
                                          "videoEpisodeInfos": []})
        with self.assertRaises(AdapterError):
            adapter.detail(BOOK)


if __name__ == "__main__":
    unittest.main()
