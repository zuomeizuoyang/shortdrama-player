"""One registry and normalization boundary for the six providers."""
import html
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from hongguo import HongguoAdapter
from overseas import create_adapter
from reelshort import ReelShortAdapter


PLATFORMS = [
    {"id": "dramabox", "name": "DramaBox", "mark": "D", "color": "#ff6e4c", "default_language": "en"},
    {"id": "reelshort", "name": "ReelShort", "mark": "R", "color": "#e75d86", "default_language": "en"},
    {"id": "dramawave", "name": "DramaWave", "mark": "W", "color": "#ac95ff", "default_language": "en"},
    {"id": "netshort", "name": "NetShort", "mark": "N", "color": "#e9bc6c", "default_language": "en"},
    {"id": "goodshort", "name": "GoodShort", "mark": "G", "color": "#6fcbb0", "default_language": "en"},
    {"id": "hongguo", "name": "红果短剧", "mark": "红", "color": "#f28754", "default_language": "zh-CN"},
]
PLATFORM_IDS = {row["id"] for row in PLATFORMS}
LANGUAGE_NAMES = {"en": "English", "es": "Español", "fr": "Français", "de": "Deutsch", "pt": "Português", "ja": "日本語", "ko": "한국어", "zh-CN": "简体中文"}


def plain(value):
    return html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))


def card(row, platform, language):
    tags = row.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if isinstance(tags, dict):
        tags = list(tags)
    normalized_tags = [plain(tag.get("name") or tag.get("text") or tag.get("label")) if isinstance(tag, dict) else plain(tag) for tag in tags]
    return {**row, "platform": platform, "drama_id": str(row["drama_id"]),
            "title": plain(row.get("title")), "description": plain(row.get("description")),
            "cover": row.get("cover") or row.get("cover_url") or "",
            "display_language": language, "tags": [tag for tag in normalized_tags if tag]}


def episode(row, platform):
    access = row.get("access")
    if access is None:
        lock = row.get("locked")
        access = "locked" if lock is True else "free" if lock is False else "unknown"
    if row.get("is_blooper"):
        label = "花絮 " + str(row.get("index", ""))
    elif row.get("index") == 0:
        label = "预告"
    else:
        label = "第 " + str(row.get("index", "")) + " 集"
    return {**row, "platform": platform, "access": access,
            "locked": access == "locked", "label": label,
            "title": plain(row.get("title"))}


class ProviderSession:
    def __init__(self):
        self.lock = threading.RLock()
        self.clients = {}
        self.touched = time.monotonic()

    def close(self):
        for client in self.clients.values():
            client.close()
        self.clients.clear()


class ProviderManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.base_url = HongguoAdapter.validate_base_url(os.environ.get("HONGGUO_BASE_URL", ""))
        self.api_key = os.environ.get("HONGGUO_API_KEY", "")
        self.managed_hongguo_url = None

    def get_session(self, key=None):
        with self.lock:
            now = time.monotonic()
            stale = [sid for sid, obj in self.sessions.items() if now - obj.touched > 7200]
            for sid in stale:
                self.sessions.pop(sid).close()
            if key not in self.sessions:
                if len(self.sessions) >= 128:
                    oldest_sid = min(self.sessions, key=lambda s: self.sessions[s].touched)
                    self.sessions.pop(oldest_sid).close()
                key = secrets.token_urlsafe(24)
                self.sessions[key] = ProviderSession()
            obj = self.sessions[key]
            obj.touched = now
            return key, obj

    def client(self, session, platform, language):
        if platform not in PLATFORM_IDS:
            raise ValueError("不支持的平台")
        if platform == "hongguo":
            language = "zh-CN"
        elif language in ("zh-CN", "zh") or language not in LANGUAGE_NAMES:
            language = "en"
        key = (platform, language)
        if key not in session.clients:
            if platform == "hongguo":
                client = HongguoAdapter(self.base_url, self.api_key)
            elif platform == "reelshort":
                client = ReelShortAdapter()
            else:
                client = create_adapter(platform)
            session.clients[key] = client
        return session.clients[key]

    def platform_info(self):
        return [{**row, "configured": bool(self.base_url) if row["id"] == "hongguo" else True,
                 "languages": [{"id": code, "name": LANGUAGE_NAMES[code]} for code in
                               (["zh-CN"] if row["id"] == "hongguo" else list(LANGUAGE_NAMES)[:-1])],
                 "note": "搜索包含官网推荐" if row["id"] == "netshort" else "官网开放集播放" if row["id"] == "hongguo" else ""}
                for row in PLATFORMS]

    def config(self):
        return {"hongguo_base_url": self.base_url, "has_api_key": bool(self.api_key),
                "configured": bool(self.base_url), "storage": "process_memory",
                "backend_type": "bundled_native" if self.base_url == self.managed_hongguo_url else "custom"}

    def update_config(self, base_url, api_key=None):
        base_url = HongguoAdapter.validate_base_url(base_url)
        with self.lock:
            self.base_url = base_url
            if api_key is not None:
                self.api_key = api_key
            for session in self.sessions.values():
                with session.lock:
                    for key in list(session.clients):
                        if key[0] == "hongguo":
                            session.clients.pop(key).close()
        return self.config()

    def rank_all(self, session, language="en"):
        items = []
        platforms_order = ["hongguo", "dramabox", "reelshort", "dramawave", "netshort", "goodshort"]
        platform_items = {}
        for p in platforms_order:
            try:
                lang = "zh-CN" if p == "hongguo" else "en"
                c = self.client(session, p, lang)
                if p == "hongguo":
                    res = c.rank(category="all", board="new_rank", language=lang)
                elif p == "dramabox":
                    res = c.rank(board="trending", language=lang)
                elif p == "reelshort":
                    res = c.rank(board="top", language=lang)
                elif p == "dramawave":
                    res = c.rank(board="most_trending_rank", language=lang)
                elif p == "netshort":
                    res = c.rank(board="trending", language=lang)
                elif p == "goodshort":
                    res = c.rank(board="Most-Trending", language=lang)
                else:
                    res = {"items": []}
                platform_items[p] = [card(row, p, lang) for row in res.get("items", [])]
            except Exception:
                platform_items[p] = []
        rank_idx = 1
        for round_i in range(5):
            for p in platforms_order:
                lst = platform_items.get(p, [])
                if round_i < len(lst):
                    item = dict(lst[round_i])
                    item["rank"] = rank_idx
                    rank_idx += 1
                    items.append(item)
        return {
            "platform": "all",
            "items": items,
            "board": "all",
            "page": 1,
            "has_more": False,
            "next_cursor": None,
            "source": "all_platforms_aggregate_rank",
            "notice": "各平台榜单聚合 · 按平台交错展示，序号不代表跨平台热度排名",
        }

    def call(self, session, platform, operation, data):
        language = data.get("language") or ("zh-CN" if platform == "hongguo" else "en")
        if platform == "hongguo":
            language = "zh-CN"
        elif language in ("zh-CN", "zh") or language not in LANGUAGE_NAMES:
            language = "en"
        with session.lock:
            if platform == "all" and operation == "rank":
                return self.rank_all(session, language)
            client = self.client(session, platform, language)
            if operation == "feed":
                raw = client.feed(cursor=data.get("cursor"), language=language)
            elif operation == "search":
                keyword = data.get("keyword", "")
                if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 150:
                    raise ValueError("请输入 1–150 字的搜索词")
                raw = client.search(keyword, cursor=data.get("cursor"), language=language)
            elif operation == "detail":
                return card(client.detail(data.get("drama_id"), language=language), platform, language)
            elif operation == "episodes":
                return {"items": [episode(row, platform) for row in client.episodes(data.get("drama_id"), language=language)]}
            elif operation == "rank":
                if platform == "hongguo":
                    raw = client.rank(category=data.get("category", "all"), board=data.get("board", "new_rank"), cursor=data.get("cursor"), language=language)
                elif platform == "dramabox":
                    raw = client.rank(board=data.get("board", "trending"), cursor=data.get("cursor"), language=language)
                elif platform == "reelshort":
                    raw = client.rank(board=data.get("board", "top"), cursor=data.get("cursor"), language=language)
                elif platform == "dramawave":
                    raw = client.rank(board=data.get("board", "most_trending_rank"), cursor=data.get("cursor"), language=language)
                elif platform == "netshort":
                    raw = client.rank(board=data.get("board", "trending"), cursor=data.get("cursor"), language=language)
                elif platform == "goodshort":
                    raw = client.rank(board=data.get("board", "Most-Trending"), cursor=data.get("cursor"), language=language)
                else:
                    raw = client.feed(cursor=data.get("cursor"), language=language)
            elif operation in {"playback", "resolvePlayback"}:
                return client.resolvePlayback(data.get("episode_id"), language=language)
            else:
                raise ValueError("不支持的操作")
            return {**raw, "items": [card(row, platform, language) for row in raw["items"]]}

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                session.close()
