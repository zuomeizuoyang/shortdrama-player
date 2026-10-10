"""Local backend for Hongguo's public website and publicly readable catalog.

Only website-accessible episodes are resolved. No native signing, app session
capture, content keys, DRM decoding, or protected-episode player requests.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

import requests

SITE = "https://hongguoduanju.com"
CATALOG_HOST = "https://api5-normal-sinfonlinea.fqnovel.com"


class PublicBackendError(RuntimeError):
    pass


def identifier(value):
    if not re.fullmatch(r"\d{1,24}", str(value)):
        raise ValueError("剧目 ID 必须为数字")
    return str(value)


def router_data(source):
    match = re.search(r"(?:window\.)?_ROUTER_DATA\s*=", source)
    if not match:
        raise PublicBackendError("官网暂时没有提供剧目数据，请稍后重试")
    try:
        state, _ = json.JSONDecoder().raw_decode(source[match.end():].lstrip())
    except ValueError as exc:
        raise PublicBackendError("官网页面数据暂时无法读取") from exc
    if not isinstance(state, dict) or not isinstance(state.get("loaderData"), dict):
        raise PublicBackendError("官网页面数据格式已变化")
    return state["loaderData"]


def official_url(sid, vid=None):
    return SITE + "/player/" + identifier(sid) + ("/" + identifier(vid) if vid else "")


class PublicHongguoClient:
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": SITE + "/"})
        self.lock = threading.RLock()
        self.cache = {}

    def close(self):
        self.session.close()

    def cached(self, key, loader, ttl=300):
        with self.lock:
            now = time.monotonic()
            for old in [k for k, v in self.cache.items() if v[0] <= now]:
                self.cache.pop(old, None)
            hit = self.cache.get(key)
            if hit:
                return hit[1]
            value = loader()
            if len(self.cache) >= 200:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic() + ttl, value)
            return value

    def page(self, path):
        # Public pages occasionally return a transient 500. One ordinary retry
        # handles that; authorization failures are never bypassed.
        for attempt in range(2):
            response = self.session.get(SITE + path, timeout=self.timeout)
            if response.status_code >= 500 and attempt == 0:
                response.close()
                continue
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise PublicBackendError("官网页面过大")
            return router_data(response.text)
        raise PublicBackendError("官网暂时无法响应")

    @staticmethod
    def card(row):
        sid = identifier(row.get("series_id"))
        categories = row.get("category_list") or []
        tags = row.get("tags") or [r.get("name", "") for r in categories if isinstance(r, dict)]
        if not tags and isinstance(row.get("category_schema"), str):
            try:
                tags = [r.get("name", "") for r in json.loads(row["category_schema"]) if isinstance(r, dict)]
            except ValueError:
                tags = []
        return {"series_id": sid, "title": row.get("series_name") or row.get("series_title") or row.get("title") or sid,
                "intro": row.get("series_intro") or row.get("video_desc") or "",
                "cover": row.get("series_cover") or row.get("cover") or "",
                "episode_cnt": row.get("episode_cnt"), "category": tags,
                "official_url": SITE + "/detail?series_id=" + sid,
                "source": "hongguo_official_web"}

    def browse(self, limit=120):
        return self.rank(category="all", board="new_rank", offset=0, limit=limit)

    def rank(self, category="all", board="new_rank", offset=0, limit=20):
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 60))

        def load():
            if category == "ai_series":
                sort_val = "hot_score"
                if board in {"new_rank", "new"}:
                    sort_val = "online_time"
                elif board == "hot_search":
                    sort_val = "hot_collect"

                params = {
                    "aid": "8662", "app_name": "novelread", "version_code": "72232",
                    "version_name": "7.2.2.32", "device_platform": "android", "os_version": "12",
                }
                body = {
                    "filter_ids": "", "req_scene": "ai_series", "offset": offset,
                    "need_selector_panel": False, "limit": limit,
                    "select_items": {
                        "genre": ["ai_series"],
                        "sort": [sort_val],
                    },
                    "session_id": "", "req_type": "only_content", "client_req_type": 3,
                }
                response = self.session.post(
                    CATALOG_HOST + "/reading/distribution/category/landpage/v",
                    params=params, json=body, timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
                if data.get("code") != 0:
                    raise PublicBackendError("红果 AI 剧集列表暂时无法读取")
                d_val = data.get("data", {})
                video_data = d_val.get("video_data", [])
                has_more = bool(d_val.get("has_more", False))
                next_offset = d_val.get("next_offset")
                if next_offset is None and has_more:
                    next_offset = offset + len(video_data)

                rows = []
                for it in video_data:
                    sid = identifier(it.get("series_id"))
                    rank_num = offset + len(rows) + 1
                    rec_info = it.get("recommend_info")
                    if rec_info and isinstance(rec_info, str):
                        try:
                            ri = json.loads(rec_info)
                            if "rank" in ri:
                                rank_num = offset + int(ri["rank"])
                        except Exception:
                            pass

                    tags = ["AI剧"]
                    if it.get("category_schema"):
                        try:
                            cats = json.loads(it["category_schema"])
                            for cat in cats:
                                if cat.get("name") and cat["name"] not in tags:
                                    tags.append(cat["name"])
                        except Exception:
                            pass

                    rec_text = ""
                    main_genre = ""
                    for sub_it in (it.get("sub_title_list") or []):
                        dt = sub_it.get("data_type")
                        cnt = sub_it.get("content", "")
                        if dt == 27 or "万" in cnt or "亿" in cnt:
                            rec_text = cnt + ("热度" if not cnt.endswith("热度") else "")
                        elif dt == 3:
                            main_genre = cnt

                    if not rec_text and it.get("play_cnt"):
                        pv = it["play_cnt"]
                        rec_text = f"{pv/10000:.1f}万热度" if pv >= 10000 else f"{pv}热度"

                    ep_cnt = it.get("episode_cnt")
                    sub_title = "AI剧"
                    if main_genre:
                        sub_title += f" · {main_genre}"
                    if ep_cnt:
                        sub_title += f" · 全{ep_cnt}集"

                    rows.append({
                        "series_id": sid,
                        "title": it.get("title") or sid,
                        "intro": it.get("video_desc") or "",
                        "cover": it.get("cover") or "",
                        "episode_cnt": ep_cnt,
                        "category": tags,
                        "official_url": SITE + "/detail?series_id=" + sid,
                        "source": "hongguo_official_ai_rank",
                        "rank": rank_num,
                        "rec_text": rec_text,
                        "sub_title": sub_title,
                        "score": it.get("score") or "",
                    })

                board_desc = "最新上架排名" if sort_val == "online_time" else ("最高热度排名" if sort_val == "hot_score" else "最高收藏与推荐排名")
                return {
                    "items": rows[:limit],
                    "has_more": has_more,
                    "next_offset": next_offset if has_more else None,
                    "category": category,
                    "board": board,
                    "offset": offset,
                    "description": f"【AI剧专区】官方 AI 生成与短剧 {board_desc} · 实时更新",
                    "count": len(rows),
                }

            # Comic, Series, or All
            sub_key = board
            tag_prefix = None
            if category == "comic_series_rank":
                tag_prefix = "漫剧"
                if board in {"new_rank", "new"}:
                    sub_key = "comic_series_new_rank"
                elif board in {"hot_play", "hot", "ranklist_hot_sc"}:
                    sub_key = "comic_series_hot_play"
                elif board == "hot_search":
                    sub_key = "hot_search"
            elif category == "series":
                tag_prefix = "真人剧"
                if board in {"hot_play", "hot"}:
                    sub_key = "hot_play"
                elif board in {"new", "new_rank"}:
                    sub_key = "new_rank"
                elif board == "hot_search":
                    sub_key = "hot_search"
            else:  # all
                if board in {"new_rank", "new"}:
                    sub_key = "comic_series_new_rank"
                elif board in {"hot_play", "hot"}:
                    sub_key = "comic_series_hot_play"
                elif board == "hot_search":
                    sub_key = "hot_search"

            params = {
                "aid": "8662", "app_name": "novelread", "version_code": "72232",
                "version_name": "7.2.2.32", "device_platform": "android", "os_version": "12",
                "cell_id": "7470092475068071998", "tab_type": "26", "client_req_type": "2",
                "client_template": "2", "screen_width_px": "1350",
                "selected_items": "comic_series_rank" if "comic_series" in sub_key else "all",
                "sub_selected_items": sub_key,
                "offset": offset, "session_id": "",
            }
            response = self.session.get(
                CATALOG_HOST + "/reading/bookapi/bookmall/cell/change/v",
                params=params, timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise PublicBackendError("红果官方榜单暂时无法读取")
            d_val = data.get("data", {})
            cell_view = d_val.get("cell_view", {})
            cell_data = cell_view.get("cell_data", [])
            has_more = d_val.get("has_more", False) or cell_view.get("has_more", False)
            next_offset = d_val.get("next_offset")
            if next_offset is None and has_more:
                next_offset = offset + 15

            rows = []
            for cell in cell_data:
                for v in cell.get("video_data", []):
                    sid = identifier(v.get("series_id"))
                    rank_num = offset + len(rows) + 1
                    rec_info = v.get("recommend_info")
                    if rec_info and isinstance(rec_info, str):
                        try:
                            ri = json.loads(rec_info)
                            if "rank" in ri:
                                rank_num = offset + int(ri["rank"])
                        except Exception:
                            pass
                    tags = []
                    if tag_prefix:
                        tags.append(tag_prefix)
                    if v.get("tag_info") and isinstance(v["tag_info"], dict) and v["tag_info"].get("text"):
                        t_text = v["tag_info"]["text"]
                        if t_text not in tags:
                            tags.append(t_text)
                    if v.get("category_schema"):
                        try:
                            cats = json.loads(v["category_schema"])
                            for cat in cats:
                                if cat.get("name") and cat["name"] not in tags:
                                    tags.append(cat["name"])
                        except Exception:
                            pass
                    if v.get("sub_title") and not tags:
                        tags.extend([t for t in v["sub_title"].split("·") if t])

                    rec_text = v.get("rec_text") or (v.get("rec_text_item") or {}).get("RecommendText") or ""
                    sub_title = v.get("sub_title") or ""
                    if tag_prefix and sub_title and not sub_title.startswith(tag_prefix):
                        sub_title = f"{tag_prefix} · {sub_title}"

                    rows.append({
                        "series_id": sid,
                        "title": v.get("title") or sid,
                        "intro": v.get("video_desc") or "",
                        "cover": v.get("cover") or "",
                        "episode_cnt": v.get("episode_cnt"),
                        "category": tags,
                        "official_url": SITE + "/detail?series_id=" + sid,
                        "source": "hongguo_official_rank",
                        "rank": rank_num,
                        "rec_text": rec_text,
                        "sub_title": sub_title,
                        "score": v.get("score") or "",
                    })
            return {
                "items": rows[:limit],
                "has_more": has_more,
                "next_offset": next_offset if has_more else None,
                "category": category,
                "board": board,
                "offset": offset,
                "description": cell_view.get("recommend_text") or "",
                "count": len(rows),
            }

        return self.cached(("rank", category, board, offset, limit), load, ttl=180)

    def search(self, keyword, limit=40):
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 150:
            raise ValueError("请输入 1–150 字的搜索词")
        keyword = keyword.strip()

        def load():
            for attempt in range(2):
                page = self.page("/search/" + quote(keyword, safe="")).get("search_(keyword)/page")
                if not isinstance(page, dict) or page.get("query") != keyword or page.get("isSuccess") is not True:
                    raise PublicBackendError("官网没有返回这个关键词的搜索结果")
                cells = page.get("searchList")
                if not isinstance(cells, list):
                    raise PublicBackendError("官网搜索首屏格式不正确")
                if cells or not int(page.get("totalCount") or 0):
                    break
                if attempt:
                    raise PublicBackendError("官网搜索暂时未返回首屏，请稍后重试")
            rows, seen = [], set()
            for cell in cells:
                video = cell.get("video_data")
                if not isinstance(video, dict) or not video.get("series_id"):
                    continue  # Actor results are not dramas.
                row = self.card(video)
                if row["series_id"] not in seen:
                    seen.add(row["series_id"])
                    rows.append(row)
            return {"results": rows[:max(1, min(int(limit), 40))], "source": "hongguo_official_web_search",
                    "total": int(page.get("totalCount") or 0), "pagination": "official_web_first_page",
                    "notice": "红果搜索显示官网首屏结果；官网总数不等于本次已返回条数。"}

        return self.cached(("search", keyword, int(limit)), load)

    def detail(self, sid):
        sid = identifier(sid)

        def load():
            page = self.page("/detail?series_id=" + sid).get("detail_page")
            if not isinstance(page, dict) or page.get("isSuccess") is not True:
                raise PublicBackendError("官网没有返回这个剧目")
            data = page.get("seriesDetail") or {}
            if str(data.get("series_id")) != sid:
                raise PublicBackendError("官网返回的剧目 ID 不一致")
            count = int(data.get("episode_cnt") or 0)
            vids = data.get("vid_list") or []
            if count < 1 or count != len(vids) or len(set(vids)) != count:
                raise PublicBackendError("官网没有返回完整集数列表")
            accessible = max(0, min(int(data.get("accessible_episode_cnt") or 0), count))
            meta = {**self.card(data), "web_accessible_episode_count": accessible,
                    "playback_scope": "official_web_accessible_episodes"}
            chapters = [{"index": n, "vid": identifier(vid), "title": f"第 {n} 集",
                         "locked": False, "official_only": n > accessible,
                         "access": "free" if n <= accessible else "official_app_required",
                         "official_url": official_url(sid, vid) if n <= accessible else SITE + "/detail?series_id=" + sid}
                        for n, vid in enumerate(vids, 1)]
            return {"meta": meta, "episodes": chapters, "source": "hongguo_official_web_detail"}

        return self.cached(("detail", sid), load)

    def resolve(self, sid, number):
        sid, number = identifier(sid), int(number)
        detail = self.detail(sid)
        chapter = next((r for r in detail["episodes"] if r["index"] == number), None)
        if not chapter:
            raise ValueError("章节不存在")
        if chapter["official_only"]:
            return {**chapter, "status": "official_player_required", "reason": "official_web_episode_limit"}

        def load():
            page = self.page("/player/" + sid + "/" + chapter["vid"])
            data = page.get("player_(series_id)/(vid)/page") or page.get("player_(series_id)/page")
            if not isinstance(data, dict) or str(data.get("series_id")) != sid or str(data.get("vid")) != chapter["vid"]:
                raise PublicBackendError("官网播放器返回的剧目或单集不一致")
            info = data.get("video_player_info") or {}
            if any(key in info for key in ("spade_a", "encrypt_info", "drm", "license_url")):
                return {**chapter, "status": "official_player_required", "reason": "protected_media"}
            url = info.get("main_url")
            parsed = urlsplit(url or "")
            if (parsed.scheme != "https" or parsed.username or parsed.password or
                not parsed.hostname or not parsed.hostname.endswith(".qznovelvod.com")):
                return {**chapter, "status": "official_player_required", "reason": "no_public_web_stream"}
            return {**chapter, "status": "playable", "url": url, "duration": info.get("duration"),
                    "format": "mp4", "source": "official_public_player_ssr"}

        return self.cached(("play", sid, number), load, ttl=180)


class BackendState:
    def __init__(self, api_key):
        self.api_key = api_key
        self.client = PublicHongguoClient()
        self.streams = {}
        self.lock = threading.Lock()

    def close(self):
        self.client.close()

    def stream_token(self, url):
        with self.lock:
            now = time.monotonic()
            for token in [t for t, r in self.streams.items() if r[0] < now]:
                self.streams.pop(token, None)
            if len(self.streams) >= 200:
                self.streams.pop(next(iter(self.streams)))
            token = secrets.token_urlsafe(24)
            self.streams[token] = (now + 600, url)
            return "/stream?token=" + token

    def get_stream(self, token):
        with self.lock:
            value = self.streams.get(token)
            if not value or value[0] < time.monotonic():
                raise ValueError("播放链接已过期，请重新选择单集")
            return value[1]


class BackendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(time.strftime("%H:%M:%S"), "Hongguo", urlsplit(self.path).path, flush=True)

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        started = False
        try:
            state = self.server.state
            path = urlsplit(self.path).path
            if path == "/health":
                self.send_json(200, {"ok": True, "backend": "official_web", "version": "0.2.0",
                                     "playback_scope": "official_web_accessible_episodes"})
                return
            if not secrets.compare_digest(self.headers.get("X-API-Key", ""), state.api_key):
                self.send_json(401, {"detail": "本地后端需要 X-API-Key"})
                return
            data = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            if path == "/browse":
                if data.get("genre", "short_play") != "short_play":
                    raise ValueError("此后端推荐片单为真人短剧")
                value = state.client.browse(data.get("limit", 120))
            elif path == "/rank":
                value = state.client.rank(
                    category=data.get("category", "all"),
                    board=data.get("board", "new_rank"),
                    offset=int(data.get("offset", 0)),
                    limit=int(data.get("limit", 20)),
                )
            elif path == "/search":
                value = state.client.search(data.get("q", ""), data.get("limit", 40))
            elif path == "/episodes":
                value = state.client.detail(data.get("series_id"))
            elif path == "/play":
                sid = identifier(data.get("series_id"))
                row = state.client.resolve(sid, data.get("ep", 1))
                row = dict(row)
                if row.get("status") == "playable":
                    row["stream_url"] = state.stream_token(row.pop("url"))
                value = {"series_id": sid, "episodes": [row], "source": "hongguo_official_web"}
            elif path == "/stream":
                url = state.get_stream(data.get("token"))
                headers = {"Referer": SITE + "/", "Accept-Encoding": "identity"}
                byte_range = self.headers.get("Range")
                if byte_range:
                    if not re.fullmatch(r"bytes=\d*-\d*", byte_range):
                        raise ValueError("不支持的 Range 请求")
                    headers["Range"] = byte_range
                # The internal API key is deliberately never sent to the CDN.
                with requests.get(url, headers=headers, timeout=30, stream=True) as response:
                    if response.status_code != 416:
                        response.raise_for_status()
                    self.send_response(response.status_code)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "video/mp4"))
                    for key in ("Content-Length", "Content-Range", "Accept-Ranges"):
                        if response.headers.get(key):
                            self.send_header(key, response.headers[key])
                    if not response.headers.get("Content-Length"):
                        self.send_header("Connection", "close")
                        self.close_connection = True
                    self.end_headers()
                    started = True
                    for chunk in response.iter_content(65536):
                        if chunk:
                            self.wfile.write(chunk)
                return
            else:
                self.send_json(404, {"detail": "接口不存在"})
                return
            self.send_json(200, value)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:
            if started:
                self.close_connection = True
                return
            message = str(exc) if isinstance(exc, (ValueError, PublicBackendError)) else "红果官网连接暂时失败，请重试"
            self.send_json(400 if isinstance(exc, ValueError) else 502, {"detail": message})


def build_backend(host="127.0.0.1", port=0, api_key=None):
    server = ThreadingHTTPServer((host, port), BackendHandler)
    server.daemon_threads = True
    server.state = BackendState(api_key or secrets.token_urlsafe(32))
    return server


def main():
    import os
    parser = argparse.ArgumentParser(description="Hongguo official public website backend")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    key = os.environ.get("HONGGUO_API_KEY")
    if not key:
        parser.error("独立运行时请设置 HONGGUO_API_KEY；Web UI 自动启动时无需手工配置")
    server = build_backend(port=args.port, api_key=key)
    print(f"Hongguo official web backend — http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.state.client.close()


if __name__ == "__main__":
    main()
