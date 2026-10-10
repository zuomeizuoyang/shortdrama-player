"""Public ReelShort drama metadata and chapter list adapters."""
from __future__ import annotations

import re
from urllib.parse import quote

from .client import ReelShortError


def _drama_id(value):
    value = str(value).strip()
    if re.fullmatch(r"[0-9a-fA-F]{24}", value):
        return value.lower()
    match = re.search(r"(?:^|-)([0-9a-fA-F]{24})(?:$|[-?/#])", value)
    if match:
        return match.group(1).lower()
    raise ValueError("drama_id must be ReelShort's 24-character book_id")


def _slug(title):
    return re.sub(r"[^\w]+", "-", title.lower(), flags=re.UNICODE).strip("-") or "drama"


class DetailsMixin:
    def _raw_detail(self, drama_id, language="en"):
        book_id = _drama_id(drama_id)
        key = (book_id, language)
        cache = getattr(self, "_details_cache", None)
        if cache is None:
            self._details_cache = cache = {}
        if key not in cache:
            response = self.request("/api/video/book/getBookInfo", method="GET",
                                    data={"book_id": book_id}, language=language)
            raw = response.get("data")
            if not isinstance(raw, dict) or raw.get("book_id") != book_id:
                raise ReelShortError("Drama details contain no matching book_id")
            cache[key] = raw
        return cache[key]

    def detail(self, drama_id, language="en"):
        raw = self._raw_detail(drama_id, language)
        book_id = raw["book_id"]
        title = raw.get("book_title", "")
        tags = [tag.get("text") for tag in raw.get("tag_list", []) if tag.get("text")]
        prefix = "" if language == "en" else "/" + quote(language, safe="-")
        return {
            "id": book_id, "drama_id": book_id, "platform": "reelshort",
            "title": title, "cover": raw.get("book_pic"),
            "description": raw.get("special_desc", ""),
            "language": raw.get("lang", language),
            "original_language": raw.get("original_lang") or None,
            "episode_count": raw.get("chapter_count", raw.get("total")),
            "tags": tags or raw.get("tag", []),
            "is_paid": raw.get("is_paid"), "paid_start": raw.get("paid_start"),
            "is_preview": raw.get("is_preview"), "is_dub": raw.get("is_dub"),
            "official_url": self.BASE_URL + prefix + "/movie/" + quote(_slug(title)) + "-" + book_id,
            "source": "getBookInfo",
        }

    def episodes(self, drama_id, language="en"):
        raw = self._raw_detail(drama_id, language)
        book_id = raw["book_id"]
        title = raw.get("book_title", "")
        source = "getChapterList"
        try:
            self.ensure_guest(language=language)
            response = self.request("/api/video/book/getChapterList", method="POST",
                                    data={"book_id": book_id}, language=language)
            data = response.get("data", {})
            chapters = data.get("chapter_lists", data.get("chapter_list"))
            if not isinstance(chapters, list):
                raise ReelShortError("Chapter list response schema changed")
        except ReelShortError as exc:
            # Public metadata is complete even when the chapter API requires a guest session.
            # These rows describe published chapters, not account entitlements.
            if exc.code not in (101, 403):
                raise
            chapters = raw.get("online_base")
            if not isinstance(chapters, list):
                raise ReelShortError("No public chapter list available") from exc
            source = "getBookInfo.online_base"
        start = raw.get("start_play") or {}
        paid_start = raw.get("paid_start")
        prefix = "" if language == "en" else "/" + quote(language, safe="-")
        results = []
        seen = set()
        for chapter in chapters:
            chapter_id = chapter.get("chapter_id")
            if not chapter_id or chapter_id in seen:
                continue
            seen.add(chapter_id)
            number = chapter.get("serial_number", chapter.get("chapter_index"))
            lock = chapter.get("is_lock")
            # The website uses 1 for locked, 2 for purchased/unlocked, and 0 for free.
            access = {0: "free", 1: "locked", 2: "unlocked"}.get(lock, "unknown")
            requires_entitlement = bool(paid_start and number is not None and number >= paid_start)
            duration = chapter.get("duration")
            if duration is None and chapter_id == start.get("chapter_id"):
                duration = start.get("duration")
            label = "Trailer" if number == 0 else "Episode " + str(number)
            path = "/episodes/" + quote(("trailer" if number == 0 else "episode-" + str(number))
                                       + "-" + _slug(title)) + "-" + book_id + "-" + chapter_id
            results.append({
                "id": chapter_id, "episode_id": book_id + ":" + chapter_id,
                "chapter_id": chapter_id, "drama_id": book_id,
                "index": number, "number": number,
                "title": chapter.get("chapter_name") or chapter.get("title") or label,
                "duration": duration, "thumbnail": chapter.get("video_pic"),
                "access": access, "is_lock": lock,
                "requires_entitlement": requires_entitlement,
                "vip_free": chapter.get("vip_free"),
                "ad_vip_locked": chapter.get("ad_vip_locked"),
                "unlock_cost": chapter.get("unlock_cost"),
                "access_source": "is_lock" if lock is not None else "unknown",
                "official_url": self.BASE_URL + prefix + path, "source": source,
            })
        results.sort(key=lambda item: item["index"] if item["index"] is not None else float("inf"))
        return results
