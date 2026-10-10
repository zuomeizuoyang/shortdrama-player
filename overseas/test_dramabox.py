"""Offline regression checks for access decisions and portable cursors."""
import unittest

from .dramabox import DramaBoxAdapter
from .common import encode_cursor


class FixtureAdapter(DramaBoxAdapter):
    def _props(self, path, language):
        book = {"bookId": "123", "bookName": "Example", "chapterCount": 2}
        if path.startswith("/drama/"):
            return {"bookInfo": book, "chapterList": [
                {"id": "1", "index": 0, "name": "1", "unlock": True,
                 "mp4": "https://media.dramaboxdb.com/free.mp4", "duration": 1000},
                {"id": "2", "index": 1, "name": "2", "unlock": False,
                 "chapterPrice": 0, "mp4": "https://media.dramaboxdb.com/locked.mp4"},
            ]}
        if path == "/":
            return {"bigList": [book], "smallData": []}
        if path.startswith("/more/"):
            section = path.split("/")[2]
            return {"pageNo": 1, "pages": 1, "moreData": {"items": [book,
                    {"bookId": str(200 + self.RECOMMENDATION_SECTIONS.index(section)), "bookName": section}]}}
        raise AssertionError(path)


class DramaBoxTests(unittest.TestCase):
    def test_locked_flag_overrides_zero_price_or_present_url(self):
        adapter = FixtureAdapter()
        playback = adapter.resolvePlayback("dramabox:en:123:2")
        self.assertEqual(playback["status"], "locked")
        self.assertIsNone(playback["url"])
        self.assertIsNone(playback["format"])

    def test_episode_identifiers_resolve_in_fresh_adapter(self):
        episode = FixtureAdapter().episodes("123")[0]
        playback = FixtureAdapter().resolvePlayback(episode["episode_id"])
        self.assertEqual(playback["status"], "playable")
        self.assertEqual(playback["format"], "mp4")
        with self.assertRaises(ValueError):
            FixtureAdapter().resolvePlayback(episode["episode_id"], language="es")

    def test_native_recommendation_cursor_is_portable_deduplicated_and_finite(self):
        result = FixtureAdapter().feed()
        ids = [item["id"] for item in result["items"]]
        while result["next_cursor"]:
            result = FixtureAdapter().feed(result["next_cursor"])
            ids.extend(item["id"] for item in result["items"])
        self.assertEqual(ids, ["123", "200", "201", "202"])
        self.assertFalse(result["has_more"])

    def test_cursor_binding_and_search_input(self):
        cursor = encode_cursor({"platform": "dramabox", "kind": "feed", "language": "en",
                                "page": 1, "section": 0, "seen_ids": []})
        with self.assertRaises(ValueError):
            FixtureAdapter().feed(cursor, language="es")
        with self.assertRaises(ValueError):
            FixtureAdapter().search("", language="en")
        with self.assertRaises(ValueError):
            FixtureAdapter().resolvePlayback("dramabox:en:../../:2")


if __name__ == "__main__":
    unittest.main()
