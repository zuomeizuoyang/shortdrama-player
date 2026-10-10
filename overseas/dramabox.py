"""DramaBox official public web adapter; preserves chapter access decisions."""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlsplit

from .common import (AdapterError, MediaVerifierMixin, OfficialWebBase,
                     decode_cursor, encode_cursor, next_data)


class DramaBoxAdapter(MediaVerifierMixin, OfficialWebBase):
    PLATFORM = "dramabox"
    BASE_URL = "https://www.dramabox.com"
    LANGUAGES = frozenset(("en", "zhHans", "ko", "zh", "es", "in", "ja", "de",
                           "fr", "pt", "ar", "th", "tl", "vi", "it", "tr", "pl"))

    def _prefix(self, language):
        if language not in self.LANGUAGES:
            language = "en"
        return "" if language == "en" else "/" + language

    def _props(self, path, language):
        data = next_data(self.get_html(self._prefix(language) + path, language=language))
        props = data.get("props", {}).get("pageProps")
        if not isinstance(props, dict):
            raise AdapterError("DramaBox page is missing its data")
        if props.get("locale", data.get("locale")) != language:
            raise AdapterError("DramaBox returned a different website language")
        return props

    @staticmethod
    def _numeric(value, name):
        value = str(value)
        if not re.fullmatch(r"\d{1,24}", value):
            raise ValueError(f"{name} must be a numeric DramaBox ID")
        return value

    def _book(self, book, language):
        bid = self._numeric(book.get("bookId"), "drama_id")
        slug = book.get("bookNameEn") or book.get("replacedBookName")
        path = self._prefix(language) + "/drama/" + bid
        if slug:
            path += "/" + quote(slug, safe="-")
        return {
            "platform": self.PLATFORM, "id": bid, "drama_id": bid,
            "title": book.get("bookName") or book.get("name", ""),
            "cover": book.get("cover") or book.get("coverWap", ""),
            "language": language, "description": book.get("introduction", ""),
            "episode_count": book.get("chapterCount", book.get("totalChapterNum")),
            "tags": book.get("tags") or book.get("labels") or [],
            "official_url": self.BASE_URL + path,
        }

    def _books(self, books, language):
        items, seen = [], set()
        for book in books or []:
            if not isinstance(book, dict) or not book.get("bookId"):
                continue
            item = self._book(book, language)
            if item["drama_id"] not in seen:
                seen.add(item["drama_id"])
                items.append(item)
        return items

    def _state(self, cursor, kind, language):
        if cursor is None:
            return {"page": 1}
        state = decode_cursor(cursor, platform=self.PLATFORM, kind=kind, language=language)
        if not isinstance(state.get("page"), int) or isinstance(state["page"], bool) or state["page"] < 1:
            raise ValueError("Cursor page must be a positive integer")
        return state

    def _page_result(self, props, language, kind, page, total_key, extra=None):
        actual_page = int(props.get("pageNo", 0))
        if actual_page != page:
            raise AdapterError("DramaBox did not honor the requested page")
        pages = int(props[total_key])
        items = self._books(props.get("bookList"), language)
        more = bool(items) and page < pages
        state = {"platform": self.PLATFORM, "kind": kind, "language": language,
                 "page": page + 1, **(extra or {})}
        return {"platform": self.PLATFORM, "language": language, "items": items,
                "page": page, "total_pages": pages,
                "next_cursor": encode_cursor(state) if more else None, "has_more": more}

    def browse(self, cursor=None, language="en"):
        """Optional native all-drama catalog, separate from recommendations."""
        self._prefix(language)
        page = self._state(cursor, "browse", language)["page"]
        path = "/browse" if page == 1 else f"/browse/0/{page}"
        props = self._props(path, language)
        result = self._page_result(props, language, "browse", page, "pages")
        result["source"] = "official_browse_catalog"
        return result

    RECOMMENDATION_SECTIONS = ("must-sees", "trending", "hidden-gems")

    def feed(self, cursor=None, language="en"):
        """Homepage recommendations followed by their native More pages.

        A portable cursor records the section, native page and IDs already
        emitted. Recommendation sections are finite and language-specific.
        """
        self._prefix(language)
        if cursor is None:
            home = self._props("/", language)
            sections = [{"name": "Featured", "items": self._books(home.get("bigList"), language)}]
            sections.extend(
                {"shelf_id": section.get("id"), "name": section.get("name", ""),
                 "items": self._books(section.get("items"), language)}
                for section in home.get("smallData", [])
            )
            items, seen = [], set()
            for section in sections:
                for item in section["items"]:
                    if item["drama_id"] not in seen:
                        seen.add(item["drama_id"])
                        items.append(item)
            state = {"platform": self.PLATFORM, "kind": "feed", "language": language,
                     "section": 0, "page": 1, "seen_ids": sorted(seen)}
            return {"platform": self.PLATFORM, "language": language, "items": items,
                    "sections": sections, "next_cursor": encode_cursor(state), "has_more": True,
                    "source": "official_home_recommendations", "pagination": "official_recommendation_sections"}
        state = self._state(cursor, "feed", language)
        section_index = state.get("section")
        if not isinstance(section_index, int) or not 0 <= section_index < len(self.RECOMMENDATION_SECTIONS):
            raise ValueError("Invalid recommendation section cursor")
        seen = state.get("seen_ids", [])
        if not isinstance(seen, list) or not all(isinstance(x, str) for x in seen):
            raise ValueError("Invalid recommendation deduplication cursor")
        seen = set(seen)
        # Skip native pages containing only homepage/earlier-section repeats.
        # Every request advances an official page or section; finite upper
        # bounds from SSR prevent a loop and no synthetic catalog is mixed in.
        for _ in range(32):
            section = self.RECOMMENDATION_SECTIONS[state["section"]]
            page = state["page"]
            path = "/more/" + section + (f"/{page}" if page > 1 else "")
            props = self._props(path, language)
            if int(props.get("pageNo", 0)) != page or not isinstance(props.get("moreData"), dict):
                raise AdapterError("DramaBox recommendation page did not honor its cursor")
            pages = int(props["pages"])
            raw = self._books(props["moreData"].get("items"), language)
            items = [item for item in raw if item["drama_id"] not in seen]
            seen.update(item["drama_id"] for item in items)
            if page < pages:
                state["page"] += 1
                more = True
            elif state["section"] + 1 < len(self.RECOMMENDATION_SECTIONS):
                state.update(section=state["section"] + 1, page=1)
                more = True
            else:
                more = False
            state["seen_ids"] = sorted(seen)
            if items or not more:
                return {"platform": self.PLATFORM, "language": language, "items": items,
                        "sections": [], "section": section, "page": page,
                        "section_total_pages": pages, "raw_item_count": len(raw),
                        "next_cursor": encode_cursor(state) if more else None, "has_more": more,
                        "source": "official_recommendation_section_ssr",
                        "pagination": "official_recommendation_sections"}
        raise AdapterError("DramaBox returned too many duplicate-only recommendation pages")

    def search(self, keyword, language="en", cursor=None):
        self._prefix(language)
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("Search keyword must be a nonempty string")
        state = self._state(cursor, "search", language)
        if cursor and state.get("keyword") != keyword:
            raise ValueError("Search cursor keyword does not match")
        query = urlencode({"searchValue": keyword, "page": state["page"]})
        props = self._props("/search?" + query, language)
        result = self._page_result(props, language, "search", state["page"], "totalPage", {"keyword": keyword})
        result.update(keyword=keyword, total=props.get("totalNum"), source="official_search_ssr")
        return result

    def _drama_props(self, drama_id, language):
        bid = self._numeric(drama_id, "drama_id")
        props = self._props("/drama/" + bid, language)
        if not isinstance(props.get("bookInfo"), dict) or not isinstance(props.get("chapterList"), list):
            raise AdapterError("DramaBox drama page is missing its book or chapter list")
        return props

    def detail(self, drama_id, language="en"):
        props = self._drama_props(drama_id, language)
        result = self._book(props["bookInfo"], language)
        result["source"] = "official_drama_ssr"
        result["episode_count_returned"] = len(props["chapterList"])
        return result

    def episodes(self, drama_id, language="en"):
        props = self._drama_props(drama_id, language)
        bid = self._numeric(props["bookInfo"]["bookId"], "drama_id")
        episodes = []
        for chapter in props["chapterList"]:
            cid = self._numeric(chapter["id"], "chapter_id")
            eid = f"dramabox:{language}:{bid}:{cid}"
            episodes.append({
                "platform": self.PLATFORM, "id": eid, "episode_id": eid, "drama_id": bid,
                "chapter_id": cid, "title": chapter.get("name", ""),
                "index": int(chapter["index"]) + 1, "language": language,
                "locked": chapter.get("unlock") is not True,
                "duration": chapter.get("duration", 0) / 1000,
                "official_url": self.BASE_URL + self._prefix(language) + f"/video/{bid}/{cid}",
            })
        episodes.sort(key=lambda item: item["index"])
        expected = props["bookInfo"].get("chapterCount")
        if expected is not None and int(expected) != len(episodes):
            raise AdapterError("DramaBox returned an incomplete chapter list")
        return episodes

    def resolvePlayback(self, episode_id, language=None):
        if not isinstance(episode_id, str):
            raise ValueError("A self-contained DramaBox episode_id is required")
        parts = episode_id.split(":")
        if len(parts) != 4 or parts[0] != self.PLATFORM:
            raise ValueError("Invalid DramaBox episode_id")
        _, embedded_language, bid, cid = parts
        if language is not None and language != embedded_language:
            raise ValueError("Episode language does not match the requested language")
        language = embedded_language
        self._prefix(language)
        bid, cid = self._numeric(bid, "drama_id"), self._numeric(cid, "chapter_id")
        props = self._drama_props(bid, language)
        chapter = next((c for c in props["chapterList"] if str(c["id"]) == cid), None)
        if chapter is None:
            raise ValueError("Episode does not belong to this drama")
        official = self.BASE_URL + self._prefix(language) + f"/video/{bid}/{cid}"
        base = {"platform": self.PLATFORM, "episode_id": episode_id, "drama_id": bid,
                "language": language, "official_url": official, "headers": {},
                "url": None, "format": None}
        # unlock is the actual access flag. chapterPrice=0 does not prove free
        # access. freeSource on locked video pages is a 15-second preview.
        if chapter.get("unlock") is not True:
            return {**base, "status": "locked", "reason": "official_chapter_locked"}
        url = chapter.get("mp4")
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            return {**base, "status": "official_player_required", "reason": "no_public_playback_url"}
        host = urlsplit(url).hostname or ""
        if not (host == "dramaboxdb.com" or host.endswith(".dramaboxdb.com")):
            raise AdapterError("DramaBox returned an unexpected media origin")
        fmt = "hls" if chapter.get("m3u8Flag") or ".m3u8" in urlsplit(url).path else "mp4"
        return {**base, "status": "playable", "url": url, "format": fmt,
                "headers": {"Referer": self.BASE_URL + "/", "User-Agent": self.USER_AGENT},
                "source": "official_unlocked_chapter_ssr"}

    def rank(self, board="trending", cursor=None, language="en"):
        self._prefix(language)
        if board not in ("trending", "must-sees", "hidden-gems"):
            board = "trending"
        page = 1
        if cursor:
            try:
                page = max(1, int(cursor))
            except (ValueError, TypeError):
                page = 1
        path = f"/more/{board}" + (f"/{page}" if page > 1 else "")
        props = self._props(path, language)
        items = []
        for i, b in enumerate(props.get("moreData", {}).get("items", [])):
            card = self._book(b, language)
            views = b.get("viewCountDisplay") or b.get("viewCount")
            card["rank"] = (page - 1) * 18 + (i + 1)
            card["rec_text"] = f"{views} 播放" if views else f"TOP {card['rank']}"
            card["sub_title"] = f"热度: {views}" if views else ""
            items.append(card)
        pages = int(props.get("pages", 1))
        has_more = page < pages
        next_cursor = str(page + 1) if has_more else None
        board_names = {"trending": "热门趋势榜", "must-sees": "必看好剧榜", "hidden-gems": "精选热播榜"}
        return {
            "platform": self.PLATFORM,
            "items": items,
            "board": board,
            "page": page,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "source": "dramabox_official_rank",
            "notice": f"DramaBox 官方{board_names.get(board, '排行榜')} · 实时播放热度",
        }
