"""Regressions for complete chapter pagination and current-chapter access."""
import unittest

from .goodshort import GoodShortAdapter
from .common import AdapterError


class FixtureAdapter(GoodShortAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []

    def _api(self, path, data, language="en"):
        self.calls.append((path, data))
        book = {"bookId": "123", "bookName": "Example", "bookResourceUrl": "example-123",
                "chapterCount": 3, "language": "ENGLISH"}
        chapter = lambda index: {"id": index + 11, "index": index,
                                 "chapterResourceUrl": f"00{index+1}-{index+11}", "price": 0,
                                 "chapterName": str(index+1)}
        if path.endswith("/book/detail"):
            return {"book": book, "chapterVoList": [chapter(0)]}
        if path.endswith("/chapter/page"):
            number = data["pageNo"]
            return {"current": number, "pages": 2, "records": [chapter(0), chapter(1)] if number == 1 else [chapter(2)]}
        if path.endswith("/chapter/detail"):
            cid = data["chapterId"]
            current = {**chapter(int(cid) - 11), "bookId": "123", "book": book,
                       "prev": {"m3u8Path": "https://v3.goodshort.com/neighbor.m3u8"}}
            if cid == "11":
                current["m3u8Path"] = "https://v3.goodshort.com/free.m3u8"
            elif cid == "12":
                current.update(price=100, m3u8Path="https://v3.goodshort.com/catalog-leak.m3u8")
            return current
        if path.endswith("/book/search/seo"):
            assert data.get("seoKeywordFormat") == data.get("keyword")
            return {"page": {"current": data["pageNo"], "pages": 1, "total": 0, "records": []}}
        raise AssertionError(path)


class GoodShortTests(unittest.TestCase):
    def test_full_chapter_list_uses_all_native_pages(self):
        adapter = FixtureAdapter()
        episodes = adapter.episodes("123")
        self.assertEqual([e["index"] for e in episodes], [1, 2, 3])
        self.assertEqual([data["pageNo"] for path, data in adapter.calls if path.endswith("/chapter/page")], [1, 2])

    def test_paid_catalog_url_is_not_playback_authorization(self):
        result = FixtureAdapter().resolvePlayback("goodshort:en:123:12")
        self.assertEqual(result["status"], "locked")
        self.assertIsNone(result["url"])

    def test_neighbor_url_is_not_used_for_current_chapter(self):
        result = FixtureAdapter().resolvePlayback("goodshort:en:123:13")
        self.assertEqual(result["status"], "official_player_required")
        self.assertIsNone(result["url"])
        self.assertEqual(FixtureAdapter().resolvePlayback("goodshort:en:123:11")["status"], "playable")

    def test_search_exact_payload_and_cursor_language(self):
        adapter = FixtureAdapter()
        result = adapter.search("nothing")
        self.assertFalse(result["has_more"])
        self.assertEqual(adapter.calls[-1][1]["seoKeywordFormat"], "nothing")
        with self.assertRaises(ValueError):
            adapter.resolvePlayback("goodshort:en:123:11", language="es")


if __name__ == "__main__":
    unittest.main()
