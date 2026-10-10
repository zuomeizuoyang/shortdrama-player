"""GoodShort's official anonymous web API, without payment or unlock calls."""
from __future__ import annotations

import math
import re
import secrets
from urllib.parse import quote, urlsplit

from .common import (AdapterError, MediaVerifierMixin, OfficialWebBase,
                     decode_cursor, encode_cursor)


class GoodShortAdapter(MediaVerifierMixin, OfficialWebBase):
    PLATFORM = "goodshort"
    BASE_URL = "https://www.goodshort.com"
    LANGUAGE_CODES = {
        "en": "ENGLISH", "id": "BAHASA_INDONESIA", "th": "THAI", "fr": "FRENCH",
        "fi": "FILIPINO", "ru": "RUSSIAN", "ja": "JAPANESE", "es": "SPANISH",
        "de": "DEUTSCH", "ko": "KOREAN", "pt": "PORTUGUESE", "ar": "ARAB",
        "zh-TW": "TRADITIONAL_CHINESE", "pl": "POLISH", "tr": "TURKISH",
    }

    def _prefix(self, language):
        if language not in self.LANGUAGE_CODES:
            language = "en"
        return "" if language == "en" else "/" + language

    @staticmethod
    def _numeric(value, name):
        value = str(value)
        if not re.fullmatch(r"\d{1,24}", value):
            raise ValueError(f"{name} must be a numeric GoodShort ID")
        return value

    def _api(self, path, data, language="en"):
        if language not in self.LANGUAGE_CODES:
            language = "en"
        self._prefix(language)
        if not path.startswith("/hwycreels/"):
            raise ValueError("Only official GoodShort web API paths are supported")
        response = self.request_http("POST", self.BASE_URL + path, json=data,
                                     headers={"platform": "WEB", "currentLanguage": language,
                                              "requestId": "GR" + str(secrets.randbelow(10**12)),
                                              "Origin": self.BASE_URL, "Referer": self.BASE_URL + "/"})
        result = response.json()
        self.request_log[-1]["api_status"] = result.get("status")
        if result.get("status") != 0:
            raise AdapterError("GoodShort website API rejected the request", code=result.get("status"))
        if not isinstance(result.get("data"), dict):
            raise AdapterError("GoodShort API response lacks its data object")
        if isinstance(result["data"].get("records"), list):
            self.request_log[-1].update(
                record_count=len(result["data"]["records"]),
                page_no=result["data"].get("current"),
                page_size=result["data"].get("size"), total=result["data"].get("total"),
                total_pages=result["data"].get("pages"),
            )
        return result["data"]

    def _book(self, book, language):
        bid = book.get("bookId") or book.get("sourceId") or (book.get("action") if book.get("actionType") == "BOOK" else None)
        bid = self._numeric(bid, "drama_id")
        actual_language = next((lang for lang, name in self.LANGUAGE_CODES.items()
                                if name == book.get("language")), language)
        resource = book.get("bookResourceUrl")
        official = self.BASE_URL + self._prefix(actual_language) + "/drama/" + quote(resource, safe="-") if resource else None
        return {"platform": self.PLATFORM, "id": bid, "drama_id": bid,
                "title": book.get("seoBookName") or book.get("bookName") or book.get("name", ""),
                "cover": book.get("cover") or book.get("bannerUrl") or "",
                "description": book.get("introduction", ""), "language": actual_language,
                "episode_count": book.get("chapterCount"),
                "tags": [tag["name"] for tag in book.get("tagsList", []) if tag.get("name")],
                "official_url": official}

    def _books(self, books, language):
        items, seen = [], set()
        for book in books or []:
            if not isinstance(book, dict) or not (book.get("bookId") or book.get("sourceId") or book.get("actionType") == "BOOK"):
                continue
            item = self._book(book, language)
            if item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
        return items

    def _state(self, cursor, kind, language):
        if cursor is None:
            return {"page": 1}
        state = decode_cursor(cursor, platform=self.PLATFORM, kind=kind, language=language)
        if not isinstance(state.get("page"), int) or isinstance(state["page"], bool) or state["page"] < 1:
            raise ValueError("Cursor page must be a positive integer")
        return state

    def feed(self, cursor=None, language="en"):
        """Homepage recommendations followed by each official column's More pages."""
        self._prefix(language)
        if cursor is None:
            home = self._api("/hwycreels/home/index", {"language": language}, language)
            if not isinstance(home.get("pageColumns"), list):
                raise AdapterError("GoodShort home API is missing its recommendation columns")
            sections = [{"name": col.get("name", ""), "column": col.get("columnResourceUrl"),
                         "items": self._books(col.get("items"), language)} for col in home["pageColumns"]]
            columns = [col["columnResourceUrl"] for col in home["pageColumns"]
                       if col.get("style") != "BANNER" and col.get("columnResourceUrl")]
            items, seen = [], set()
            for section in sections:
                for item in section["items"]:
                    if item["id"] not in seen:
                        seen.add(item["id"])
                        items.append(item)
            state = {"platform": self.PLATFORM, "kind": "feed", "language": language,
                     "columns": columns, "column_index": 0, "page": 1, "seen_ids": sorted(seen)}
            return {"platform": self.PLATFORM, "language": language, "items": items,
                    "sections": sections, "next_cursor": encode_cursor(state) if columns else None,
                    "has_more": bool(columns), "source": "official_home_api",
                    "pagination": "official_recommendation_columns"}
        state = self._state(cursor, "feed", language)
        columns, index, seen_ids = state.get("columns"), state.get("column_index"), state.get("seen_ids")
        if not isinstance(columns, list) or not columns or len(columns) > 32 or not all(isinstance(x, str) and len(x) < 200 for x in columns):
            raise ValueError("Invalid GoodShort recommendation column cursor")
        if not isinstance(index, int) or not 0 <= index < len(columns):
            raise ValueError("Invalid GoodShort recommendation column index")
        if not isinstance(seen_ids, list) or not all(isinstance(x, str) for x in seen_ids):
            raise ValueError("Invalid GoodShort recommendation deduplication cursor")
        seen = set(seen_ids)
        for _ in range(64):
            column, page = columns[state["column_index"]], state["page"]
            data = self._api("/hwycreels/home/second/list", {
                "columnResourceUrl": column, "pageNo": page, "pageSize": 20,
                "index": 0, "language": language,
            }, language)
            raw = self._books(data.get("items"), language)
            total = int(data.get("totalSize", 0))
            pages = max(1, math.ceil(total / 20))
            items = [item for item in raw if item["id"] not in seen]
            seen.update(item["id"] for item in items)
            if page < pages:
                state["page"] += 1
                more = True
            elif state["column_index"] + 1 < len(columns):
                state.update(column_index=state["column_index"] + 1, page=1)
                more = True
            else:
                more = False
            state["seen_ids"] = sorted(seen)
            if items or not more:
                return {"platform": self.PLATFORM, "language": language, "items": items,
                        "sections": [], "column": column, "page": page, "column_total": total,
                        "column_total_pages": pages, "raw_item_count": len(raw),
                        "has_more": more, "next_cursor": encode_cursor(state) if more else None,
                        "source": "official_recommendation_column_api",
                        "pagination": "official_recommendation_columns"}
        raise AdapterError("GoodShort returned too many duplicate-only recommendation pages")

    def search(self, keyword, language="en", cursor=None):
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("Search keyword must be a nonempty string")
        state = self._state(cursor, "search", language)
        if cursor and state.get("keyword") != keyword:
            raise ValueError("Search cursor keyword does not match")
        data = self._api("/hwycreels/book/search/seo", {
            "seoKeywordFormat": keyword, "keyword": keyword, "pageNo": state["page"],
            "pageSize": 18, "language": language, "type": "DEFAULT",
            "isSetCookiesAgain": "", "seoSearch": True,
        }, language)
        page = data.get("page")
        if page is None:
            return {"platform": self.PLATFORM, "language": language, "keyword": keyword,
                    "items": [], "page": state["page"], "total": 0, "total_pages": 0,
                    "has_more": False, "next_cursor": None, "source": "official_search_api"}
        if int(page.get("current", 0)) != state["page"]:
            raise AdapterError("GoodShort did not honor the requested search page")
        items = self._books(page.get("records"), language)
        pages = int(page["pages"])
        more = bool(items) and state["page"] < pages
        next_state = {"platform": self.PLATFORM, "kind": "search", "language": language,
                      "keyword": keyword, "page": state["page"] + 1}
        return {"platform": self.PLATFORM, "language": language, "keyword": keyword,
                "items": items, "page": state["page"], "total": page.get("total"),
                "total_pages": pages, "has_more": more,
                "next_cursor": encode_cursor(next_state) if more else None, "source": "official_search_api"}

    def _detail_data(self, drama_id, language):
        bid = self._numeric(drama_id, "drama_id")
        data = self._api("/hwycreels/book/detail", {"bookId": bid}, language)
        if not isinstance(data.get("book"), dict) or str(data["book"].get("bookId")) != bid:
            raise AdapterError("GoodShort returned the wrong drama")
        return data

    def detail(self, drama_id, language="en"):
        result = self._book(self._detail_data(drama_id, language)["book"], language)
        result["source"] = "official_book_detail_api"
        return result

    def episodes(self, drama_id, language="en"):
        book = self._detail_data(drama_id, language)["book"]
        bid, expected = self._numeric(book["bookId"], "drama_id"), int(book["chapterCount"])
        actual_language = self._book(book, language)["language"]
        records, page_no = [], 1
        while True:
            data = self._api("/hwycreels/chapter/page", {"bookId": bid, "pageNo": page_no, "pageSize": 100}, language)
            if int(data.get("current", 0)) != page_no or not isinstance(data.get("records"), list):
                raise AdapterError("GoodShort chapter pagination is inconsistent")
            records.extend(data["records"])
            if page_no >= int(data["pages"]):
                break
            if page_no >= 100 or not data["records"]:
                raise AdapterError("GoodShort chapter list did not finish")
            page_no += 1
        if len(records) != expected or len(set(str(c["id"]) for c in records)) != expected:
            raise AdapterError("GoodShort returned an incomplete or duplicate chapter list")
        episodes = []
        for chapter in records:
            cid = self._numeric(chapter["id"], "chapter_id")
            eid = f"goodshort:{actual_language}:{bid}:{cid}"
            official = self.BASE_URL + self._prefix(actual_language) + "/episode/" + quote(book["bookResourceUrl"], safe="-") + "/" + quote(chapter["chapterResourceUrl"], safe="-")
            episodes.append({"platform": self.PLATFORM, "id": eid, "episode_id": eid,
                             "drama_id": bid, "chapter_id": cid, "language": actual_language,
                             "title": chapter.get("seoChapterName") or chapter.get("chapterName", ""),
                             "index": int(chapter["index"]) + 1, "locked": float(chapter.get("price", 0)) > 0,
                             "price": chapter.get("price"), "access_source": "public_catalog_price",
                             "duration": chapter.get("playTime"), "official_url": official})
        return sorted(episodes, key=lambda item: item["index"])

    def resolvePlayback(self, episode_id, language=None):
        if not isinstance(episode_id, str) or len(episode_id.split(":")) != 4:
            raise ValueError("A self-contained GoodShort episode_id is required")
        platform, embedded_language, bid, cid = episode_id.split(":")
        if platform != self.PLATFORM or language is not None and language != embedded_language:
            raise ValueError("Episode platform or language does not match")
        language = embedded_language
        self._prefix(language)
        bid, cid = self._numeric(bid, "drama_id"), self._numeric(cid, "chapter_id")
        data = self._api("/hwycreels/chapter/detail", {"bookId": bid, "chapterId": cid, "num": 16}, language)
        if str(data.get("id")) != cid or str(data.get("bookId")) != bid:
            raise AdapterError("GoodShort returned a different chapter")
        book = data.get("book", {})
        official = self.BASE_URL + self._prefix(language) + "/episode/" + quote(book.get("bookResourceUrl", ""), safe="-") + "/" + quote(data.get("chapterResourceUrl", ""), safe="-")
        base = {"platform": self.PLATFORM, "episode_id": episode_id, "drama_id": bid,
                "language": language, "official_url": official, "url": None, "format": None, "headers": {}}
        # Only the requested chapter is inspected. Catalog/prev/next entries
        # can contain a URL for another chapter and prove no access entitlement.
        if float(data.get("price", 0)) > 0:
            return {**base, "status": "locked", "reason": "official_chapter_requires_coins"}
        url = data.get("m3u8Path")
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            return {**base, "status": "official_player_required", "reason": "no_authorized_chapter_url"}
        host = urlsplit(url).hostname or ""
        if not (host == "goodshort.com" or host.endswith(".goodshort.com")):
            raise AdapterError("GoodShort returned an unexpected media origin")
        return {**base, "status": "playable", "url": url,
                "format": "hls" if ".m3u8" in urlsplit(url).path else "mp4",
                "headers": {"Referer": self.BASE_URL + "/", "User-Agent": self.USER_AGENT},
                "source": "official_free_chapter_detail_api"}

    def rank(self, board="Most-Trending", cursor=None, language="en"):
        if language not in self.LANGUAGE_CODES:
            language = "en"
        self._prefix(language)
        if board not in ("Most-Trending", "Top-in-GoodShort", "Hot-List", "Popular-Now"):
            board = "Most-Trending"
        page = 1
        if cursor:
            try:
                page = max(1, int(cursor))
            except (ValueError, TypeError):
                page = 1
        page_size = 20
        data = self._api("/hwycreels/home/second/list", {
            "columnResourceUrl": board, "pageNo": page, "pageSize": page_size,
            "index": 0, "language": language
        }, language)

        def _fmt(n):
            if not n:
                return ""
            try:
                num = float(n)
                if num >= 1_000_000:
                    return f"{num / 1_000_000:.1f}M 播放"
                if num >= 1_000:
                    return f"{num / 1_000:.1f}K 播放"
                return f"{int(num)} 播放"
            except Exception:
                return str(n)

        items = []
        for i, it in enumerate(data.get("items", [])):
            c = self._book(it, language)
            views = it.get("viewCount")
            c["rank"] = (page - 1) * page_size + (i + 1)
            c["rec_text"] = _fmt(views) if views else f"TOP {c['rank']}"
            c["sub_title"] = f"热度: {_fmt(views)}" if views else ""
            items.append(c)
        total = int(data.get("totalSize", len(items)))
        pages = max(1, math.ceil(total / page_size))
        has_more = page < pages
        next_cursor = str(page + 1) if has_more else None
        board_names = {"Most-Trending": "飙升趋势榜", "Top-in-GoodShort": "官方必看总榜", "Hot-List": "热度榜", "Popular-Now": "热门榜"}
        return {
            "platform": self.PLATFORM,
            "items": items,
            "board": board,
            "page": page,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "source": "goodshort_official_rank",
            "notice": f"GoodShort 官方{board_names.get(board, '排行榜')} · 真实播放量排行",
        }
