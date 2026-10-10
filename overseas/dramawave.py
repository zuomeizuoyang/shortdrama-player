"""DramaWave's official H5 API; no sister-platform substitution or unlocking."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urljoin, urlsplit

from .common import (AdapterError, MediaVerifierMixin, OfficialWebBase,
                     decode_cursor, encode_cursor)


class DramaWaveAdapter(MediaVerifierMixin, OfficialWebBase):
    PLATFORM = "dramawave"
    BASE_URL = "https://m.mydramawave.com"
    API_URL = "https://api.mydramawave.com"
    _READ_PATHS = {
        "/h5-api/anonymous/login", "/h5-api/homepage/v2/tab/list",
        "/h5-api/homepage/v2/tab/index", "/h5-api/homepage/v2/tab/feed",
        "/h5-api/search/drama", "/h5-api/drama/info",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._device_id = secrets.token_hex(16)
        self._auth = {}
        self._config = None
        self._details = {}

    def _load_config(self):
        if self._config is not None:
            return self._config
        html = self.get_html("/")
        scripts = re.findall(r'<script[^>]*src=["\']([^"\']+)', html)
        src = next((src for src in scripts if "/assets/index-" in src), None)
        if not src:
            raise AdapterError("DramaWave official browser bundle changed")
        src = urljoin(self.BASE_URL, src)
        if urlsplit(src).hostname != "static-v1.mydramawave.com":
            raise AdapterError("Unexpected DramaWave configuration origin")
        js = self.request_http("GET", src).text
        key = re.search(r'ii="([^"]+)"', js)
        app = re.search(r'o=Sr\.md5\(`([^&]+)&\$\{n\.auth_secret', js)
        versions = re.search(r'"app-version":t\?Kh\(\):"([^"]+)"', js)
        locale_rows = re.findall(r'\{shortCode:"([^"]+)",name:"[^"]*",language:"([^"]+)",'
                                 r'timezone:"([^"]+)",country:"([^"]+)"\}', js)
        if not (key and app and versions and locale_rows):
            raise AdapterError("DramaWave browser protocol needs review")
        locales = {}
        for short, locale, timezone, country in locale_rows:
            row = {"shortCode": short, "language": locale, "timezone": timezone, "country": country}
            locales[short] = locales[locale] = row
        self._config = {"key": key.group(1).encode(), "app_secret": app.group(1),
                        "version": versions.group(1), "locales": locales}
        return self._config

    @staticmethod
    def _aes():
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad, unpad
        except ImportError as exc:
            raise AdapterError("DramaWave web transport requires pycryptodome") from exc
        return AES, pad, unpad

    def _locale(self, language):
        config = self._load_config()
        if language not in config["locales"]:
            language = "en"
        return config["locales"][language]

    def _request(self, path, *, method="GET", data=None, language="en", guest=True):
        if path not in self._READ_PATHS:
            raise ValueError("Only catalog reads and normal anonymous initialization are supported")
        cfg = self._load_config()
        locale = self._locale(language)
        if guest and not self._auth:
            self._login(language)
        headers = {"app-name": "com.dramawave.h5", "app-version": cfg["version"],
                   "device-hash": self._device_id, "device-id": self._device_id,
                   "device": "h5", "Content-Type": "application/json",
                   "Origin": self.BASE_URL, "Referer": self.BASE_URL + "/", **locale}
        if self._auth:
            signature = hashlib.md5((cfg["app_secret"] + "&" + self._auth["auth_secret"]).encode()).hexdigest()
            headers["authorization"] = ("oauth_signature=" + signature + ",oauth_token="
                                        + self._auth["auth_key"] + ",ts=" + str(int(time.time() * 1000)))
        if method.upper() == "POST":
            AES, pad, _ = self._aes()
            iv = secrets.token_bytes(16)
            payload = json.dumps(data or {}, ensure_ascii=False, separators=(",", ":")).encode()
            encrypted = base64.b64encode(iv + AES.new(cfg["key"], AES.MODE_CBC, iv).encrypt(pad(payload, 16))).decode()
            response = self.request_http("POST", self.API_URL + path, data=encrypted, headers=headers)
        else:
            response = self.request_http("GET", self.API_URL + path, params=data, headers=headers)
        try:
            result = response.json()
        except ValueError:
            AES, _, unpad = self._aes()
            raw = base64.b64decode(response.text, validate=True)
            result = json.loads(unpad(AES.new(cfg["key"], AES.MODE_CBC, raw[:16]).decrypt(raw[16:]), 16))
        if not isinstance(result, dict):
            raise AdapterError("Unexpected DramaWave API response")
        code = result.get("code")
        self.request_log[-1]["code"] = code
        if code not in {200, 0}:
            raise AdapterError("DramaWave API rejected the request", code=code,
                               http_status=response.status_code)
        return result.get("data") or {}

    def _login(self, language):
        data = self._request("/h5-api/anonymous/login", method="POST",
                             data={"device_id": self._device_id}, language=language, guest=False)
        if not data.get("success") or not data.get("auth_key") or not data.get("auth_secret"):
            raise AdapterError("DramaWave anonymous initialization failed")
        self._auth = {key: data[key] for key in ("auth_key", "auth_secret")}

    def _card(self, raw, language):
        drama_id = str(raw.get("id") or raw.get("key") or "")
        if not drama_id:
            raise AdapterError("DramaWave card contains no series ID")
        return {"id": drama_id, "drama_id": drama_id, "platform": self.PLATFORM,
                "title": raw.get("name") or raw.get("title") or "",
                "cover": raw.get("cover"), "description": raw.get("desc") or "",
                "episode_count": raw.get("episode_count"), "language": language,
                "tags": raw.get("series_tag") or raw.get("content_tags") or [],
                "official_url": self.BASE_URL + "/series/" + drama_id}

    def _page(self, rows, data, *, language, kind, state, source):
        seen = set(state.get("seen", []))
        items = []
        for raw in rows:
            if not isinstance(raw, dict) or not (raw.get("id") or raw.get("key")):
                continue
            card = self._card(raw, language)
            if card["drama_id"] not in seen:
                seen.add(card["drama_id"])
                items.append(card)
        page = data.get("page_info") or {}
        next_value = page.get("next") or ""
        has_more = bool(page.get("has_more"))
        if has_more and (not next_value or next_value == state.get("next")):
            raise AdapterError("DramaWave pagination did not advance")
        if has_more and not items:
            raise AdapterError("DramaWave returned a repeated or empty pagination page")
        if len(seen) > 1500:
            raise AdapterError("Cursor catalog exceeds this adapter's pagination bound")
        next_state = {**state, "platform": self.PLATFORM, "kind": kind, "language": language,
                      "next": next_value, "seen": sorted(seen), "page": state.get("page", 0) + 1}
        return {"items": items, "platform": self.PLATFORM, "language": language,
                "has_more": has_more, "next_cursor": encode_cursor(next_state) if has_more else None,
                "page": next_state["page"], "source": source}

    def feed(self, cursor=None, language="en"):
        if cursor is None:
            tabs = self._request("/h5-api/homepage/v2/tab/list", language=language).get("list") or []
            if not tabs:
                raise AdapterError("DramaWave homepage returned no catalog tab")
            tab = next((tab for tab in tabs if tab.get("business_name") == "popular"), tabs[0])
            data = self._request("/h5-api/homepage/v2/tab/index", language=language,
                                 data={"tab_key": tab["tab_key"], "position_index": tab["position_index"], "first": ""})
            modules = data.get("items") or []
            recommend = next((module for module in modules if module.get("type") == "recommend"), {})
            state = {"module_key": recommend.get("module_key", ""), "page": 0}
            rows = [row for module in modules for row in module.get("items", [])]
        else:
            state = decode_cursor(cursor, platform=self.PLATFORM, kind="feed", language=language)
            if not state.get("module_key") or not state.get("next"):
                raise ValueError("Feed cursor is missing server pagination state")
            data = self._request("/h5-api/homepage/v2/tab/feed", method="POST", language=language,
                                 data={"module_key": state["module_key"], "next": state["next"]})
            rows = data.get("items") or []
        return self._page(rows, data, language=language, kind="feed", state=state,
                          source="DramaWave.h5_homepage_v2")

    def search(self, keyword, cursor=None, language="en"):
        keyword = str(keyword).strip()
        if not keyword:
            raise ValueError("Search keyword must not be empty")
        state = {"keyword": keyword, "page": 0}
        if cursor is not None:
            state = decode_cursor(cursor, platform=self.PLATFORM, kind="search", language=language)
            if state.get("keyword") != keyword or not state.get("next"):
                raise ValueError("Search cursor does not match keyword")
        data = self._request("/h5-api/search/drama", method="POST", language=language,
                             data={"scence": "series_tag", "next": state.get("next", ""),
                                   "keyword": keyword, "timestamp": str(int(time.time() * 1000))})
        # The official UI adds /search/security recommendations after an empty
        # query. Keep search matches honest by returning the actual empty result.
        result = self._page(data.get("items") or [], data, language=language, kind="search",
                            state=state, source="DramaWave.h5_search_drama")
        result["keyword"] = keyword
        return result

    @staticmethod
    def _id(value):
        value = str(value)
        if not re.fullmatch(r"[A-Za-z0-9]+", value):
            raise ValueError("DramaWave series ID must be alphanumeric")
        return value

    def _raw_detail(self, drama_id, language):
        drama_id = self._id(drama_id)
        key = (drama_id, language)
        cached = self._details.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        data = self._request("/h5-api/drama/info", data={"series_id": drama_id}, language=language)
        raw = data.get("info")
        if not isinstance(raw, dict) or str(raw.get("id")) != drama_id:
            raise AdapterError("DramaWave details returned no matching series")
        entries = raw.get("episode_list")
        declared_count = raw.get("episode_count")
        if not isinstance(entries, list) or not isinstance(declared_count, int):
            raise AdapterError("DramaWave details contain no complete episode list")
        main_count = sum(entry.get("is_blooper") is not True for entry in entries)
        if main_count != declared_count:
            raise AdapterError("DramaWave main episode list is incomplete: "
                               f"declared {declared_count}, returned {main_count}")
        self._details[key] = (time.monotonic(), raw)
        return raw

    def detail(self, drama_id, language="en"):
        raw = self._raw_detail(drama_id, language)
        return {**self._card(raw, language), "original_language": raw.get("original_audio_language"),
                "finish_status": raw.get("finish_status"), "listing_status": raw.get("listing_status"),
                "free": raw.get("free"), "pay_index": raw.get("pay_index"),
                "episode_count_returned": len(raw["episode_list"]),
                "bonus_episode_count": sum(ep.get("is_blooper") is True for ep in raw["episode_list"]),
                "source": "DramaWave.h5_drama_info"}

    def episodes(self, drama_id, language="en"):
        raw = self._raw_detail(drama_id, language)
        results = []
        for episode in raw.get("episode_list") or []:
            chapter_id = str(episode["id"])
            flag = episode.get("unlock")
            access = "locked" if flag is False else "free" if flag is True and episode.get("video_type") == "free" else "unlocked" if flag is True else "unknown"
            results.append({"id": chapter_id, "chapter_id": chapter_id, "drama_id": raw["id"],
                            "episode_id": raw["id"] + ":" + chapter_id, "platform": self.PLATFORM,
                            "number": episode.get("index"), "index": episode.get("index"),
                            "title": episode.get("name"), "duration": episode.get("duration"),
                            "thumbnail": episode.get("cover"), "access": access, "unlock": flag,
                            "video_type": episode.get("video_type"), "h5_available": episode.get("h5_available"),
                            "is_blooper": episode.get("is_blooper") is True,
                            "blooper_title": raw.get("blooper_title") if episode.get("is_blooper") is True else None,
                            "official_url": self.BASE_URL + "/series/" + raw["id"] + "/" + chapter_id,
                            "source": "DramaWave.h5_drama_info.episode_list"})
        return sorted(results, key=lambda row: row["number"] if isinstance(row["number"], int) else float("inf"))

    def resolvePlayback(self, episode_id, language="en"):
        match = re.fullmatch(r"([A-Za-z0-9]+):([A-Za-z0-9]+)", str(episode_id))
        if not match:
            raise ValueError("episode_id must be series_id:episode_id")
        drama_id, chapter_id = match.groups()
        base = {"episode_id": episode_id, "drama_id": drama_id, "chapter_id": chapter_id,
                "platform": self.PLATFORM, "language": language, "status": "official_page",
                "official_url": self.BASE_URL + "/series/" + drama_id + "/" + chapter_id,
                "url": None, "format": None, "headers": {}, "expires_at": None}
        try:
            raw = self._raw_detail(drama_id, language)
        except AdapterError as exc:
            return {**base, "status": "login_required" if exc.code in {401, 403} else "official_page",
                    "reason": "official_api_unavailable", "code": exc.code}
        episode = next((ep for ep in raw.get("episode_list", []) if str(ep.get("id")) == chapter_id), None)
        if episode is None:
            return {**base, "status": "not_found", "reason": "unknown_episode"}
        base.update(number=episode.get("index"), duration=episode.get("duration"))
        if episode.get("unlock") is False:
            return {**base, "status": "locked", "reason": "official_episode_locked"}
        if episode.get("unlock") is not True:
            return {**base, "reason": "access_unconfirmed"}
        if episode.get("h5_available") is not True:
            return {**base, "reason": "official_app_required"}
        url = (episode.get("external_audio_h264_m3u8") or episode.get("m3u8_url")
               or episode.get("video_url") or episode.get("external_audio_h265_m3u8"))
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
            return {**base, "reason": "no_authorized_stream"}
        fmt = "hls" if urlsplit(url).path.lower().endswith(".m3u8") else "mp4" if urlsplit(url).path.lower().endswith(".mp4") else None
        if not fmt:
            return {**base, "reason": "official_player_format"}
        subtitles = [{"language": sub.get("language"), "url": sub.get("vtt") or sub.get("subtitle")}
                     for sub in episode.get("subtitle_list") or []]
        return {**base, "status": "playable", "url": url, "format": fmt,
                "headers": {"Referer": self.BASE_URL + "/"}, "audio_languages": episode.get("audio") or [],
                "subtitles": subtitles, "source": "DramaWave.authorized_episode_info"}

    def rank(self, board="most_trending_rank", cursor=None, language="en"):
        tabs = self._request("/h5-api/homepage/v2/tab/list", language=language).get("list") or []
        if not tabs:
            raise AdapterError("DramaWave homepage returned no catalog tab")
        tab = tabs[0]
        data = self._request("/h5-api/homepage/v2/tab/index", language=language,
                             data={"tab_key": tab["tab_key"], "position_index": tab["position_index"], "first": ""})
        modules = data.get("items") or []
        rank_card = None
        for m in modules:
            for it in m.get("items", []):
                card = it.get("module_card") or {}
                if card.get("module_type") == "most_trending_rank" or it.get("title") == "Rankings":
                    rank_card = card
                    break
            if rank_card:
                break
        items = []
        raw_items = rank_card.get("items", []) if rank_card else []
        for i, row in enumerate(raw_items):
            series_id = str(row.get("key") or row.get("id") or "")
            if not series_id:
                continue
            hot = row.get("hot_score")
            card = {
                "id": series_id,
                "drama_id": series_id,
                "platform": self.PLATFORM,
                "title": row.get("title") or row.get("name") or "",
                "cover": row.get("cover"),
                "description": row.get("desc") or "",
                "episode_count": row.get("episode_count") or row.get("update_count"),
                "language": language,
                "tags": row.get("series_tag") or row.get("content_tags") or [],
                "official_url": self.BASE_URL + "/series/" + series_id,
                "rank": i + 1,
                "rec_text": f"{hot} 热度" if hot else f"TOP {i + 1}",
                "sub_title": f"热度: {hot}" if hot else "",
            }
            items.append(card)
        board_names = {"most_trending_rank": "飙升趋势榜", "top_search_rank": "热搜榜", "new_release_rank": "新剧榜"}
        return {
            "platform": self.PLATFORM,
            "items": items,
            "board": board,
            "page": 1,
            "has_more": False,
            "next_cursor": None,
            "source": "dramawave_official_rank",
            "notice": f"DramaWave 官方{board_names.get(board, '排行榜')} · 实时热度排名",
        }
