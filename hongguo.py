"""Five-method bridge to the bundled public-web or a configured Hongguo backend.

Uses the documented backend contract. Does not ship native signing, content
keys or protected-video decryption. The public website backend is separate.
"""
import re
import time
from urllib.parse import urljoin, urlsplit

from overseas.common import AdapterError, OfficialWebBase


class HongguoAdapter(OfficialWebBase):
    PLATFORM = "hongguo"

    def __init__(self, base_url="", api_key="", **kwargs):
        super().__init__(**kwargs)
        self.BASE_URL = base_url.rstrip("/")
        self.api_key = api_key
        self._details = {}

    @staticmethod
    def validate_base_url(value):
        if not value:
            return ""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("请输入 http:// 或 https:// 开头的后端地址，不要附带密钥或查询参数")
        return value.rstrip("/")

    def _api(self, path, params):
        if not self.BASE_URL:
            raise AdapterError("请先在连接设置中填写红果后端地址", code="configuration_required")
        response = self.request_http("GET", self.BASE_URL + path, params=params,
                                     headers={"X-API-Key": self.api_key})
        value = response.json()
        if not isinstance(value, dict):
            raise AdapterError("红果后端返回格式不正确")
        return value

    @staticmethod
    def _id(value):
        if not re.fullmatch(r"\d{1,24}", str(value)):
            raise ValueError("红果剧目 ID 必须为数字")
        return str(value)

    def _card(self, row):
        drama_id = self._id(row.get("series_id") or row.get("drama_id"))
        return {"platform": self.PLATFORM, "id": drama_id, "drama_id": drama_id,
                "title": row.get("title", drama_id), "cover": row.get("cover", ""),
                "description": row.get("intro", ""), "episode_count": row.get("episode_cnt"),
                "language": "zh-CN", "tags": row.get("category") or [],
                "official_url": row.get("official_url") or "https://hongguoduanju.com/detail?series_id=" + drama_id,
                "web_accessible_episode_count": row.get("web_accessible_episode_count"),
                "playback_scope": row.get("playback_scope"),
                "rank": row.get("rank"),
                "rec_text": row.get("rec_text", ""),
                "sub_title": row.get("sub_title", ""),
                "score": row.get("score", "")}

    def feed(self, cursor=None, language="zh-CN"):
        if language not in {"zh-CN", "zh"}:
            raise ValueError("红果后端内容语言为简体中文")
        result = self.rank(category="all", board="new_rank", cursor=cursor, language=language)
        result.update(source="hongguo_official_new_rank", feed_label="红果新剧榜",
                      notice="红果官方新剧榜；官网开放集可播放，其余剧集请前往官方平台。")
        return result

    def rank(self, category="all", board="new_rank", cursor=None, language="zh-CN"):
        if language not in {"zh-CN", "zh"}:
            raise ValueError("红果后端内容语言为简体中文")
        offset = 0
        if cursor is not None:
            try:
                offset = int(cursor)
            except (ValueError, TypeError):
                offset = 0
        raw = self._api("/rank", {"category": category, "board": board, "offset": offset, "limit": 20})
        items = [self._card(row) for row in raw.get("items", [])]
        next_cursor = str(raw["next_offset"]) if raw.get("has_more") and raw.get("next_offset") is not None else None
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "category": category,
            "board": board,
            "platform": self.PLATFORM,
            "source": "hongguo_official_rank",
            "notice": "红果官方榜单 · 实时热度排名",
            "description": raw.get("description", ""),
        }

    def search(self, keyword, language="zh-CN", cursor=None):
        if language not in {"zh-CN", "zh"}:
            raise ValueError("红果后端内容语言为简体中文")
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("请输入搜索词")
        # Snapshot kind binds the search keyword as well as platform/language.
        items = None
        if cursor is None:
            raw = self._api("/search", {"q": keyword, "limit": 40})
            items = [self._card(row) for row in raw.get("results", [])]
        result = self.snapshot_page(items, cursor=cursor, language=language, kind="search:" + keyword)
        result.update(keyword=keyword, source="hongguo_backend_search", result_limit=40)
        if cursor is None:
            for field in ("total", "notice", "pagination"):
                if field in raw:
                    result[field] = raw[field]
        return result

    def _raw_detail(self, drama_id):
        drama_id = self._id(drama_id)
        cached = self._details.get(drama_id)
        if not cached or time.monotonic() - cached[0] >= 30:
            raw = self._api("/episodes", {"series_id": drama_id})
            meta, chapters = raw.get("meta"), raw.get("episodes")
            if not isinstance(meta, dict) or not isinstance(chapters, list):
                raise AdapterError("红果后端缺少剧目详情或章节列表")
            if str(meta.get("series_id")) != drama_id:
                raise AdapterError("红果后端返回了其他剧目")
            if meta.get("episode_cnt") is not None and int(meta["episode_cnt"]) != len(chapters):
                raise AdapterError("红果章节列表不完整")
            self._details[drama_id] = (time.monotonic(), raw)
        return self._details[drama_id][1]

    def detail(self, drama_id, language="zh-CN"):
        result = self._card(self._raw_detail(drama_id)["meta"])
        result["source"] = "hongguo_backend_episodes_meta"
        return result

    def episodes(self, drama_id, language="zh-CN"):
        drama_id = self._id(drama_id)
        raw = self._raw_detail(drama_id)
        output = []
        for chapter in raw["episodes"]:
            index = int(chapter["index"])
            if index < 1:
                raise AdapterError("红果后端章节序号无效")
            output.append({"platform": self.PLATFORM, "drama_id": drama_id,
                           "episode_id": f"hongguo:{drama_id}:{index}",
                           "chapter_id": str(chapter.get("vid", index)), "index": index,
                           "title": chapter.get("title") or f"第 {index} 集",
                           "duration": chapter.get("duration"), "locked": chapter.get("locked"),
                           "access": chapter.get("access") or ("unknown" if "locked" not in chapter else "locked" if chapter["locked"] else "unlocked"),
                           "official_only": chapter.get("official_only", False),
                           "official_url": chapter.get("official_url") or "https://hongguoduanju.com/detail?series_id=" + drama_id})
        if len({row["episode_id"] for row in output}) != len(output):
            raise AdapterError("红果后端章节列表有重复")
        return sorted(output, key=lambda row: row["index"])

    def resolvePlayback(self, episode_id, language="zh-CN"):
        match = re.fullmatch(r"hongguo:(\d{1,24}):(\d{1,5})", str(episode_id))
        if not match:
            raise ValueError("请使用集数接口返回的红果 episode_id")
        drama_id, number = match.groups()
        chapter = next((row for row in self.episodes(drama_id, language) if row["index"] == int(number)), None)
        if chapter is None:
            raise ValueError("红果章节不存在")
        base = {"platform": self.PLATFORM, "episode_id": episode_id, "drama_id": drama_id,
                "url": None, "format": None, "headers": {}, "official_url": chapter["official_url"]}
        if chapter.get("locked") is True:
            return {**base, "status": "locked", "reason": "backend_episode_locked"}
        if chapter.get("official_only"):
            return {**base, "status": "official_player_required", "reason": "official_web_episode_limit"}
        data = self._api("/play", {"series_id": drama_id, "ep": number})
        row = next((row for row in data.get("episodes", []) if int(row.get("index", 0)) == int(number)), None)
        if not row:
            return {**base, "status": "not_found"}
        if row.get("locked") is True:
            return {**base, "status": "locked"}
        if row.get("status") == "official_player_required":
            return {**base, "status": row["status"], "reason": row.get("reason"),
                    "official_url": row.get("official_url") or base["official_url"]}
        # Only the configured service's playback endpoint is accepted. Never
        # consume encrypted_url, backup ciphertext, keys or raw DRM metadata.
        stream = row.get("stream_url")
        if not isinstance(stream, str) or not stream:
            return {**base, "status": "official_player_required", "reason": "backend_no_playable_stream"}
        url = urljoin(self.BASE_URL + "/", stream)
        if urlsplit(url)[:2] != urlsplit(self.BASE_URL)[:2]:
            raise AdapterError("红果后端播放入口必须属于已配置的服务")
        return {**base, "status": "playable", "url": url, "format": "mp4",
                "headers": {"X-API-Key": self.api_key}, "source": "configured_backend_stream"}
