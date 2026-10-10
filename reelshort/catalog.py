"""ReelShort public web catalog and its native pagination.

Homepage/search are read from the same server-rendered JSON used by the
official web app. Feed continuation uses its authenticated anonymous API
request helper, supplied by the shared client.
"""

import base64
import json
import re
import secrets
from urllib.parse import urlencode


LANGUAGES = frozenset((
    "en", "pt", "es", "de", "fr", "ja", "ko", "th", "ru", "id", "zh-TW",
    "ar", "pl", "it", "tr", "ro", "cs", "bg", "vi",
))


def language_prefix(language):
    if language not in LANGUAGES:
        language = "en"
    return "" if language == "en" else "/" + language


def page_props(html):
    match = re.search(
        r'<script\b(?=[^>]*\bid=[\"\']__NEXT_DATA__[\"\'])[^>]*>(.*?)</script>',
        html, re.S,
    )
    if not match:
        raise RuntimeError("ReelShort page does not contain __NEXT_DATA__")
    data = json.loads(match.group(1))
    props = data.get("props", {}).get("pageProps")
    if not isinstance(props, dict):
        raise RuntimeError("ReelShort page is missing pageProps")
    return props


def normalize_book(book, language="en"):
    return {
        "platform": "reelshort",
        "drama_id": str(book["book_id"]),
        "id": str(book["book_id"]),
        "title": book.get("book_title", ""),
        "cover_url": book.get("book_pic") or book.get("default_pic") or "",
        "cover": book.get("book_pic") or book.get("default_pic") or "",
        "description": book.get("special_desc", ""),
        "language": book.get("lang", language),
        "episode_count": book.get("chapter_count"),
        "book_type": book.get("book_type"),
        "tags": book.get("theme") or [],
    }


def _books(books, language):
    result, seen = [], set()
    for book in books or []:
        if not isinstance(book, dict) or not book.get("book_id"):
            continue
        key = str(book["book_id"])
        if key not in seen:
            seen.add(key)
            result.append(normalize_book(book, language))
    return result


def _encode_cursor(data):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor, kind, language):
    if not isinstance(cursor, str) or not cursor or len(cursor) > 16384:
        raise ValueError("Invalid ReelShort cursor")
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid ReelShort cursor") from exc
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("kind") != kind:
        raise ValueError("Cursor is not valid for this operation")
    if data.get("language") != language:
        raise ValueError("Cursor language does not match the requested language")
    if not isinstance(data.get("page"), int) or data["page"] < 1:
        raise ValueError("Cursor page must be a positive integer")
    return data


class CatalogMixin:
    feed_page_size = 24

    def _feed_snapshot_page(self, snapshot_id, offset, language):
        snapshots = getattr(self, "_reelshort_feed_snapshots", {})
        snapshot = snapshots.get(snapshot_id)
        if snapshot is None:
            raise ValueError("Feed snapshot expired or belongs to another client; restart feed")
        if snapshot["language"] != language:
            raise ValueError("Feed snapshot language does not match")
        size = snapshot["page_size"]
        end = offset + size
        if offset < 0 or offset >= len(snapshot["items"]) or offset % size:
            raise ValueError("Invalid feed snapshot offset")
        next_cursor = _encode_cursor({
            "v": 1, "kind": "feed", "language": language,
            "page": end // size + 1, "snapshot": snapshot_id, "offset": end,
        }) if end < len(snapshot["items"]) else snapshot["waterfall_cursor"]
        return {
            "platform": "reelshort", "language": language,
            "items": snapshot["items"][offset:end],
            "sections": snapshot["sections"] if offset == 0 else [],
            "hall_id": snapshot["hall_id"], "snapshot_total": len(snapshot["items"]),
            "page": offset // size + 1, "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "ssr_finished_hint": snapshot["ssr_finished_hint"],
            "source": "official_web_home_snapshot",
            "pagination": "local_snapshot_then_official_waterfall",
        }

    def feed(self, cursor=None, language="en"):
        """Return the public home shelves, then the official waterfall.

        Home shelves are flattened and deduplicated in official display order,
        then served as stable local pages (24 by default). Snapshot cursors are
        scoped to this client instance. After the local snapshot, the official
        waterfall is queried even when SSR says finished: live verification
        found one additional book behind that hint, followed by an empty page.
        """
        prefix = language_prefix(language)
        if cursor is not None:
            state = _decode_cursor(cursor, "feed", language)
            if "snapshot" in state:
                if not isinstance(state.get("offset"), int):
                    raise ValueError("Feed cursor lacks its snapshot offset")
                return self._feed_snapshot_page(state["snapshot"], state["offset"], language)
            if not isinstance(state.get("bs_id"), int) or not state.get("last_book_id"):
                raise ValueError("Feed cursor lacks its shelf or last book")
            payload = {
                "bs_id": state["bs_id"], "last_book_id": state["last_book_id"],
                "page": state["page"], "limit": 42,
            }
            if state.get("batch_id") is not None:
                payload["batch_id"] = state["batch_id"]
            response = self.request(
                "/api/ms/hall/webHallWaterfallPages", data=payload, language=language,
            )
            if response.get("code") != 0 or not isinstance(response.get("data"), dict):
                raise RuntimeError(f"ReelShort waterfall failed: {response.get('code')}")
            data = response["data"]
            if "lists" not in data and data.get("is_finished") != 1:
                raise RuntimeError("ReelShort waterfall response lacks pagination fields")
            raw_items = _books(data.get("lists", []), language)
            # Live API can omit is_finished entirely. Empty pages or a repeated
            # last ID must end pagination so callers never spin indefinitely.
            finished = data.get("is_finished") == 1 or not raw_items
            if raw_items and raw_items[-1]["drama_id"] == state["last_book_id"]:
                finished = True
            seen = set(state.get("seen_ids", []))
            items = [item for item in raw_items if item["drama_id"] not in seen]
            seen.update(item["drama_id"] for item in items)
            if not items:
                finished = True
            next_cursor = None
            if not finished:
                state.update(page=state["page"] + 1,
                             last_book_id=raw_items[-1]["drama_id"], seen_ids=sorted(seen))
                next_cursor = _encode_cursor(state)
            return {
                "platform": "reelshort", "language": language, "items": items,
                "sections": [], "next_cursor": next_cursor, "has_more": not finished,
                "source": "official_web_waterfall_api",
                "is_finished": data.get("is_finished"),
                "raw_item_count": len(raw_items),
                "end_reason": ("official_finished" if data.get("is_finished") == 1
                               else "empty_page" if not raw_items
                               else "duplicate_only_page" if not items
                               else "repeated_last_book" if finished else None),
                "pagination": "official_waterfall",
            }

        props = page_props(self.get_html(prefix + "/", language=language))
        hall = props.get("fallback", {}).get("/api/ms/hall/webInfo")
        if not isinstance(hall, dict) or not isinstance(hall.get("bookShelfList"), list):
            raise RuntimeError("ReelShort home is missing its public catalog")
        sections, raw_books, waterfall = [], [], None
        for shelf in hall["bookShelfList"]:
            books = shelf.get("books", [])
            raw_books.extend(books)
            if books:
                sections.append({
                    "shelf_id": shelf["bs_id"], "name": shelf.get("bookshelf_name", ""),
                    "style": shelf.get("ui_style"), "items": _books(books, language),
                })
            if shelf.get("ui_style") == 5004:
                waterfall = shelf
        next_cursor = None
        if waterfall and waterfall.get("books"):
            state = {
                "v": 1, "kind": "feed", "language": language, "page": 1,
                "bs_id": waterfall["bs_id"],
                "last_book_id": waterfall["books"][-1]["book_id"],
                "seen_ids": [item["drama_id"] for item in _books(raw_books, language)],
            }
            if waterfall.get("batch_id") is not None:
                state["batch_id"] = waterfall["batch_id"]
            next_cursor = _encode_cursor(state)
        items = _books(raw_books, language)
        if not items:
            raise RuntimeError("ReelShort home contains no public book entries")
        size = self.feed_page_size
        if not isinstance(size, int) or size < 1 or size > 100:
            raise ValueError("feed_page_size must be an integer between 1 and 100")
        if not hasattr(self, "_reelshort_feed_snapshots"):
            self._reelshort_feed_snapshots = {}
        # Bound memory. Evicted cursors report expiration instead of silently
        # refetching a reordered homepage and duplicating/skipping entries.
        while len(self._reelshort_feed_snapshots) >= 8:
            del self._reelshort_feed_snapshots[next(iter(self._reelshort_feed_snapshots))]
        snapshot_id = secrets.token_urlsafe(12)
        self._reelshort_feed_snapshots[snapshot_id] = {
            "items": items, "sections": sections, "language": language,
            "hall_id": hall.get("hallId"), "page_size": size,
            "waterfall_cursor": next_cursor,
            "ssr_finished_hint": waterfall.get("is_finished") if waterfall else None,
        }
        return self._feed_snapshot_page(snapshot_id, 0, language)

    def search(self, keyword, language="en", cursor=None):
        """Search the official server-rendered movie results (12 per page).

        ReelShort may return its own suggestions for an unmatched keyword;
        these remain the official search results and are not filtered locally.
        """
        prefix = language_prefix(language)
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("Search keyword must be a nonempty string")
        page = 1
        if cursor is not None:
            state = _decode_cursor(cursor, "search", language)
            if state.get("keyword") != keyword:
                raise ValueError("Cursor keyword does not match the requested keyword")
            page = state["page"]
        query = urlencode({"keywords": keyword, "page": page, "type": "movies"})
        props = page_props(self.get_html(prefix + "/search?" + query, language=language))
        if not isinstance(props.get("books"), list) or "totalPage" not in props:
            raise RuntimeError("ReelShort search page lacks result pagination")
        items = _books(props["books"], language)
        total_pages = int(props["totalPage"])
        actual_page = int(props.get("page", page))
        has_more = bool(props.get("nextPageLink")) and actual_page < total_pages
        next_cursor = _encode_cursor({
            "v": 1, "kind": "search", "language": language,
            "keyword": keyword, "page": actual_page + 1,
        }) if has_more else None
        return {
            "platform": "reelshort", "language": language, "keyword": keyword,
            "items": items, "page": actual_page, "total": int(props.get("total", 0)),
            "total_pages": total_pages, "next_cursor": next_cursor,
            "has_more": has_more, "source": "official_web_search_ssr",
        }

    def rank(self, board="top", cursor=None, language="en"):
        prefix = language_prefix(language)
        html = self.get_html(prefix + "/", language=language)
        props = page_props(html)
        fb = props.get("fallback", {})
        hall = fb.get("/api/ms/hall/webInfo")
        if not hall:
            for k, v in fb.items():
                if isinstance(v, dict) and "bookShelfList" in v:
                    hall = v
                    break
        shelves = (hall.get("bookShelfList") if hall else []) or []
        target_name = "TOP" if board == "top" else "New Release" if board == "new_release" else "Reel Original 🌏" if board == "reel_original" else "TOP"
        shelf = next((s for s in shelves if s.get("bookshelf_name") == target_name), None)
        if not shelf and shelves:
            shelf = next((s for s in shelves if s.get("ui_style") == 5002), shelves[0])

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
        raw_books = (shelf.get("books") if shelf else []) or []
        for i, b in enumerate(raw_books):
            c = normalize_book(b, language)
            reads = b.get("read_count")
            collects = b.get("collect_count")
            c["rank"] = i + 1
            c["rec_text"] = _fmt(reads) if reads else f"TOP {i + 1}"
            c["sub_title"] = f"{_fmt(reads)}" + (f" · {_fmt(collects).replace('播放', '收藏')}" if collects else "")
            items.append(c)
        board_names = {"top": "总排行榜", "new_release": "新剧榜", "reel_original": "独家原创榜"}
        return {
            "platform": "reelshort",
            "items": items,
            "board": board,
            "page": 1,
            "has_more": False,
            "next_cursor": None,
            "source": "reelshort_official_rank",
            "notice": f"ReelShort 官方{board_names.get(board, '排行榜')} · 真实播放量排行",
        }
