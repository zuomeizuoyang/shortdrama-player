"""NetShort's official web APIs, including its published transport protocol.

Only episodes explicitly reported as unlocked are resolved. Published browser
transport configuration is read at runtime; account credentials are not saved.
"""
from __future__ import annotations

import base64
import html
import json
import re
import time
from urllib.parse import quote, urljoin, urlsplit

from .common import AdapterError, OfficialWebBase, MediaVerifierMixin, encode_cursor, decode_cursor


LANGUAGES = {"en": "en_US", "zh": "zh_TW", "ja": "ja_JP", "ko": "ko_KR",
             "es": "es_ES", "th": "th_TH", "id": "id_ID", "pt": "pt_PT",
             "cn": "zh_CN", "it": "it_IT", "de": "de_DE", "fr": "fr_FR",
             "ar": "ar_AE", "ms": "ms_MY", "tr": "tr_TR", "vi": "vi_VN", "hi": "hi_IN"}


def _id(value):
    value = str(value)
    if not re.fullmatch(r"\d{7,20}", value):
        raise ValueError("NetShort IDs must be 7 to 20 decimal digits")
    return value


def _plain(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value or ""))


class NetShortAdapter(MediaVerifierMixin, OfficialWebBase):
    BASE_URL = "https://netshort.com"
    PLATFORM = "netshort"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._config = None
        self._details = {}

    def _language(self, language):
        if language not in LANGUAGES:
            language = "en"
        return LANGUAGES[language]

    def _browser_config(self):
        if self._config:
            return self._config
        page = self.get_html("/")
        scripts = re.findall(r'<script[^>]+src="([^\"]+)"', page)
        candidates = [u for u in scripts if re.search(r"/chunks/\d+-", u)]
        candidates.sort(key=lambda u: 0 if "/8000-" in u else 1)
        for path in candidates:
            js = self.request_http("GET", urljoin(self.BASE_URL, path)).text
            public = re.search(r'setPublicKey\("(.*?)"\)', js)
            private = re.search(r'setPrivateKey\("(.*?)"\)', js)
            key = re.search(r'return"([A-Za-z0-9]{32})"\}\)\(\)', js)
            if public and private and key:
                self._config = {"public": json.loads('"' + public.group(1) + '"'),
                                "response": json.loads('"' + private.group(1) + '"'),
                                "key": key.group(1).encode(), "bundle_path": path}
                return self._config
        raise AdapterError("NetShort browser protocol changed; published configuration unavailable")

    def _api(self, path, body, language="en"):
        from Crypto.Cipher import AES, PKCS1_v1_5
        from Crypto.PublicKey import RSA
        from Crypto.Util.Padding import pad, unpad
        config = self._browser_config()
        key = config["key"]
        encrypted_key = base64.b64encode(PKCS1_v1_5.new(RSA.import_key(config["public"])).encrypt(
            base64.b64encode(key))).decode()
        ciphertext = base64.b64encode(AES.new(key, AES.MODE_ECB).encrypt(pad(
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(), 16))).decode()
        response = self.request_http("POST", self.BASE_URL + "/prod-web-api" + path,
                                     data=ciphertext, headers={
                                         "Content-Language": self._language(language),
                                         "Content-Type": "application/json;charset=utf-8",
                                         "OS": "4", "canary": "v1", "version": "1.2.0",
                                         "encrypt-key": encrypted_key,
                                         "Origin": self.BASE_URL, "Referer": self.BASE_URL + "/"})
        try:
            result = response.json()
        except ValueError:
            encrypted_response_key = response.headers.get("encrypt-key")
            if not encrypted_response_key:
                raise AdapterError("NetShort response has no published transport key")
            raw_key = PKCS1_v1_5.new(RSA.import_key(config["response"])).decrypt(
                base64.b64decode(encrypted_response_key), b"")
            response_key = base64.b64decode(raw_key)
            result = json.loads(unpad(AES.new(response_key, AES.MODE_ECB).decrypt(
                base64.b64decode(response.text)), 16))
        if not isinstance(result, dict):
            raise AdapterError("NetShort API response schema changed")
        if result.get("code") != 200:
            raise AdapterError(_plain(result.get("msg", "NetShort API error")), code=result.get("code"))
        self.request_log[-1]["code"] = result["code"]
        return result["data"]

    def _card(self, item, language):
        return {"id": str(item["shortPlayId"]), "drama_id": str(item["shortPlayId"]),
                "platform": self.PLATFORM, "title": _plain(item.get("shortPlayNameNoHL") or item.get("shortPlayName")),
                "cover": item.get("shortPlayCover"), "description": _plain(item.get("shotIntroduce")),
                "episode_count": item.get("totalEpisode"), "language": language,
                "official_url": urljoin(self.BASE_URL, item.get("shortPlayNameUrl") or item.get("shortPlayUrl") or "/")}

    def feed(self, cursor=None, language="en"):
        self._language(language)
        items = None
        if cursor is None:
            raw = self._api("/web/v5/short_play/load_carousel_chartV5", {}, language)
            items = [self._card(item, language) for item in raw]
            # Next.js RSC contains the complete homepage recommendation groups.
            page = self.get_html("/" if language == "en" else "/" + language, language)
            decoder = json.JSONDecoder()
            stream = ""
            for match in re.finditer(r'self\.__next_f\.push\(', page):
                try:
                    row, _ = decoder.raw_decode(page[match.end():])
                    if len(row) > 1 and isinstance(row[1], str):
                        stream += row[1]
                except (ValueError, TypeError):
                    continue
            marker = '"videoListGroup":'
            start = stream.find(marker)
            if start < 0:
                raise AdapterError("NetShort homepage recommendation schema changed")
            groups, _ = decoder.raw_decode(stream[start + len(marker):])
            for group in groups:
                for item in group.get("data") or []:
                    card = self._card(item, language)
                    card["recommendation_group"] = group.get("groupName")
                    items.append(card)
        result = self.snapshot_page(items, cursor=cursor, language=language, page_size=24)
        result["source"] = "official_home_all_groups_snapshot"
        return result

    def search(self, keyword, cursor=None, language="en"):
        self._language(language)
        keyword = str(keyword).strip()
        if not keyword:
            raise ValueError("Search keyword must not be empty")
        page = 1
        seen = set()
        if cursor:
            state = decode_cursor(cursor, platform=self.PLATFORM, kind="search", language=language)
            if state.get("keyword") != keyword or not isinstance(state.get("page"), int) or state["page"] < 2:
                raise ValueError("Search cursor keyword or page mismatch")
            page = state["page"]
            seen = set(state.get("seen", []))
        raw = self._api("/web/short_play/search/keyword/seo", {
            "queryKeyword": keyword, "pageQuery": {"pageNum": page, "pageSize": 10}}, language)
        rows = raw.get("list", [])
        items = []
        for row in rows:
            drama_id = str(row["shortPlayId"])
            if drama_id not in seen and len(seen) < 500:
                items.append(self._card(row, language))
                seen.add(drama_id)
        total = raw.get("totalHits")
        # The public API may append recommendation rows and overstate totalHits.
        # Mirror the website's short-page termination, with repeat-page protection.
        more = len(rows) >= 10 and bool(items) and len(seen) < 500
        termination = ("adapter_result_limit" if len(seen) >= 500 else
                       "repeated_page" if rows and not items else
                       "official_short_page" if len(rows) < 10 else None)
        next_cursor = encode_cursor({"platform": self.PLATFORM, "kind": "search", "language": language,
                                     "keyword": keyword, "page": page + 1,
                                     "seen": sorted(seen)}) if more else None
        return {"items": items, "next_cursor": next_cursor, "has_more": more, "reported_total": total,
                "page": page, "platform": self.PLATFORM, "language": language, "keyword": keyword,
                "source": "official_search_api", "total_is_reliable": False,
                "pagination": "official_page_with_recommendation_backfill",
                "matching_semantics": "official_search_and_recommendations",
                "termination_reason": termination}

    def _raw_detail(self, drama_id, language):
        drama_id = _id(drama_id)
        cache_key = (drama_id, language)
        cached = self._details.get(cache_key)
        if cached is None or time.monotonic() - cached[0] >= 30:
            raw = self._api("/web/v4/short_play/detail_info/cascade_label", {
                "type": "episode", "language": language, "shortPlayId": drama_id}, language)
            if str(raw.get("shortPlayId")) != drama_id:
                raise AdapterError("NetShort detail ID mismatch")
            chapters = raw.get("videoEpisodeInfos")
            if not isinstance(chapters, list):
                raise AdapterError("NetShort detail has no published episode list")
            official_count = raw.get("totalEpisode")
            if official_count is not None and int(official_count) != len(chapters):
                raise AdapterError("NetShort published episode list is incomplete")
            self._details[cache_key] = (time.monotonic(), raw)
        return self._details[cache_key][1]

    def detail(self, drama_id, language="en"):
        raw = self._raw_detail(drama_id, language)
        result = self._card(raw, language)
        count = len(raw["videoEpisodeInfos"])
        official_count = raw.get("totalEpisode")
        result.update(episode_count=int(official_count) if official_count is not None else count,
                      episode_count_returned=count, episode_count_official=official_count,
                      episode_count_source="totalEpisode" if official_count is not None else "published_episode_list",
                      tags=list((raw.get("shortPlayLabels") or {}).keys()),
                      delisted=raw.get("isDelisted"), source="official_detail_api")
        return result

    def episodes(self, drama_id, language="en"):
        raw = self._raw_detail(drama_id, language)
        drama_id = str(raw["shortPlayId"])
        base = urljoin(self.BASE_URL, raw["shortPlayUrl"])
        result = []
        for row in raw.get("videoEpisodeInfos", []):
            chapter = _id(row["episodeId"])
            number = row["episodeNo"]
            locked = row.get("isLock")
            result.append({"id": chapter, "episode_id": drama_id + ":" + chapter,
                           "chapter_id": chapter, "drama_id": drama_id, "platform": self.PLATFORM,
                           "index": number, "number": number, "title": "Episode " + str(number),
                           "duration": None, "thumbnail": row.get("episodeCover"),
                           "access": "locked" if locked is True else "free" if locked is False else "unknown",
                           "is_lock": locked, "locked": locked,
                           "access_source": "official_detail_api.isLock",
                           "official_url": base + "-ep-" + str(number)})
        return sorted(result, key=lambda row: row["index"])

    def resolvePlayback(self, episode_id, language="en"):
        if not isinstance(episode_id, str) or episode_id.count(":") != 1:
            raise ValueError("episode_id must contain drama_id:chapter_id")
        drama_id, chapter_id = map(_id, episode_id.split(":"))
        chapter = next((row for row in self.episodes(drama_id, language) if row["id"] == chapter_id), None)
        if chapter is None:
            raise ValueError("Episode does not belong to the supplied drama")
        result = {"episode_id": episode_id, "drama_id": drama_id, "chapter_id": chapter_id,
                  "official_url": chapter["official_url"], "platform": self.PLATFORM,
                  "url": None, "format": None, "headers": {}, "access": chapter["access"]}
        if chapter["access"] != "free":
            result.update(status="locked" if chapter["access"] == "locked" else "official_player_required",
                          reason="official_episode_locked" if chapter["access"] == "locked" else "entitlement_unknown")
            return result
        raw = self._api("/web/v4/short_play/episode_info", {
            "shortPlayId": drama_id, "episodeNo": chapter["index"]}, language)
        if str(raw.get("episodeId")) != chapter_id:
            raise AdapterError("NetShort playback episode ID mismatch")
        if raw.get("isLock") is not False:
            result.update(status="locked" if raw.get("isLock") is True else "official_player_required",
                          reason="official_playback_not_authorized")
            return result
        media = raw.get("playVoucher")
        if not isinstance(media, str) or urlsplit(media).scheme != "https":
            result.update(status="official_player_required", reason="no_public_media_url")
            return result
        result.update(status="playable", url=media, format="hls" if ".m3u8" in media else "mp4",
                      duration=float(raw["duration"]) if raw.get("duration") else None,
                      headers={"Referer": chapter["official_url"], "User-Agent": self.USER_AGENT},
                      source="official_episode_api")
        return result

    def rank(self, board="trending", cursor=None, language="en"):
        self._language(language)
        if board == "carousel":
            raw = self._api("/web/v5/short_play/load_carousel_chartV5", {}, language)
            items = []
            for i, it in enumerate(raw or []):
                c = self._card(it, language)
                c["rank"] = i + 1
                c["rec_text"] = f"TOP {i + 1}"
                c["sub_title"] = f"共 {c.get('episode_count') or 0} 集"
                items.append(c)
            return {
                "platform": self.PLATFORM, "items": items, "board": board,
                "page": 1, "has_more": False, "next_cursor": None,
                "source": "netshort_official_carousel_chart",
                "notice": "NetShort 官方轮播榜 · 精选推荐",
            }
        page = self.get_html("/" if language == "en" else "/" + language, language)
        decoder = json.JSONDecoder()
        stream = ""
        for match in re.finditer(r'self\.__next_f\.push\(', page):
            try:
                row, _ = decoder.raw_decode(page[match.end():])
                if len(row) > 1 and isinstance(row[1], str):
                    stream += row[1]
            except (ValueError, TypeError):
                continue
        marker = '"videoListGroup":'
        start = stream.find(marker)
        if start < 0:
            raise AdapterError("NetShort homepage recommendation schema changed")
        groups, _ = decoder.raw_decode(stream[start + len(marker):])
        target_keyword = "Trending" if board == "trending" else "New" if board == "new_releases" else "Watch" if board == "watch_now" else "Trending"
        group = next((g for g in groups if target_keyword.lower() in g.get("groupName", "").lower()), groups[0])
        items = []
        for i, it in enumerate(group.get("data") or []):
            c = self._card(it, language)
            c["rank"] = i + 1
            c["rec_text"] = f"TOP {i + 1}"
            c["sub_title"] = f"共 {c.get('episode_count') or 0} 集"
            items.append(c)
        board_names = {"trending": "趋势热播榜", "new_releases": "新上线榜", "watch_now": "必看精选榜", "carousel": "轮播榜"}
        return {
            "platform": self.PLATFORM, "items": items, "board": board,
            "page": 1, "has_more": False, "next_cursor": None,
            "source": "netshort_official_rank",
            "notice": f"NetShort 官方{board_names.get(board, '排行榜')} · 实时趋势",
        }
