"""Shared HTTP, finite cursors and small media probes for official web adapters."""
from __future__ import annotations

import base64
import json
import re
import secrets
import zlib
from urllib.parse import urljoin, urlsplit

import requests


class AdapterError(RuntimeError):
    def __init__(self, message, *, code=None, http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def next_data(html):
    match = re.search(r'<script\b(?=[^>]*\bid=["\']__NEXT_DATA__["\'])[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise AdapterError("Official page does not contain Next.js page data")
    return json.loads(match.group(1))


def encode_cursor(state):
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > 1_048_576:
        raise AdapterError("Cursor state exceeds the adapter capacity")
    compressed = zlib.compress(raw)
    if len(compressed) + 3 < len(raw):
        result = "z1:" + base64.urlsafe_b64encode(compressed).decode().rstrip("=")
    else:
        result = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if len(result) > 32768:
        raise AdapterError("Cursor exceeds the adapter capacity")
    return result


def decode_cursor(cursor, *, platform, kind, language):
    if not isinstance(cursor, str) or not cursor or len(cursor) > 32768:
        raise ValueError("Invalid cursor")
    try:
        compressed = cursor.startswith("z1:")
        value = cursor[3:] if compressed else cursor
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if compressed:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(raw, 1_048_577)
            if len(raw) > 1_048_576 or not decoder.eof or decoder.unused_data:
                raise ValueError("Invalid or oversized compressed cursor")
        state = json.loads(raw)
    except Exception as exc:
        raise ValueError("Invalid cursor") from exc
    if not isinstance(state, dict) or any(state.get(k) != v for k, v in
                                         {"platform": platform, "kind": kind, "language": language}.items()):
        raise ValueError("Cursor platform, operation or language mismatch")
    return state


class OfficialWebBase:
    USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

    def __init__(self, *, timeout=30, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self.request_log = []
        self._snapshots = {}

    def request_http(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        response = self.session.request(method, url, **kwargs)
        parsed = urlsplit(response.url)
        self.request_log.append({"method": method.upper(), "host": parsed.hostname,
                                 "path": parsed.path, "http_status": response.status_code})
        response.raise_for_status()
        return response

    def get_html(self, path, language="en"):
        return self.request_http("GET", urljoin(self.BASE_URL, path),
                                 headers={"Accept-Language": language}).text

    def snapshot_page(self, items=None, *, cursor=None, language="en", page_size=24, kind="feed"):
        if cursor is None:
            unique, seen = [], set()
            for item in items or []:
                key = str(item["drama_id"])
                if key not in seen:
                    seen.add(key)
                    unique.append(item)
            if not unique:
                return {"items": [], "next_cursor": None, "has_more": False,
                        "snapshot_total": 0, "platform": self.PLATFORM, "language": language}
            while len(self._snapshots) >= 8:
                self._snapshots.pop(next(iter(self._snapshots)))
            sid = secrets.token_urlsafe(12)
            self._snapshots[sid] = unique
            offset = 0
        else:
            state = decode_cursor(cursor, platform=self.PLATFORM, kind=kind, language=language)
            sid, offset = state.get("snapshot"), state.get("offset")
            if sid not in self._snapshots:
                raise ValueError("Snapshot expired or belongs to another adapter instance")
            if not isinstance(offset, int) or offset < 0 or offset % page_size:
                raise ValueError("Invalid snapshot offset")
            unique = self._snapshots[sid]
            if offset >= len(unique):
                raise ValueError("Snapshot offset is beyond the end")
        end = offset + page_size
        cursor = encode_cursor({"platform": self.PLATFORM, "kind": kind, "language": language,
                                "snapshot": sid, "offset": end}) if end < len(unique) else None
        return {"items": unique[offset:end], "next_cursor": cursor, "has_more": cursor is not None,
                "snapshot_total": len(unique), "platform": self.PLATFORM, "language": language,
                "page": offset // page_size + 1, "source": "official_home_snapshot"}

    def close(self):
        self.session.close()


class MediaVerifierMixin:
    def verify_stream(self, playback):
        return self._verify_stream(playback, seen=frozenset(), depth=0)

    def _verify_stream(self, playback, *, seen, depth):
        if playback.get("status") != "playable":
            return {"verified": False, "reason": "not_playable", "status": playback.get("status")}
        url = playback["url"]
        headers = dict(playback.get("headers") or {})
        timeout = getattr(self, "timeout", 30)
        evidence = {"format": playback["format"], "cdn_host": urlsplit(url).hostname}
        if playback["format"] == "hls":
            if url in seen or depth >= 3:
                return {**evidence, "verified": False, "reason": "hls_cycle_or_depth_limit"}
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            text = response.text
            evidence.update(manifest_status=response.status_code, manifest_bytes=len(response.content),
                            manifest_content_type=response.headers.get("Content-Type"))
            if not text.lstrip().startswith("#EXTM3U"):
                return {**evidence, "verified": False, "reason": "invalid_hls_manifest"}
            if re.search(r"#EXT-X-(?:SESSION-)?KEY:.*METHOD=(?!NONE)", text):
                return {**evidence, "verified": False, "reason": "encrypted_media_official_player_required"}
            lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
            if not lines:
                return {**evidence, "verified": False, "reason": "empty_hls_manifest"}
            media_url = urljoin(url, lines[0])
            if "#EXT-X-STREAM-INF" in text:
                return self._verify_stream({**playback, "url": media_url}, seen=seen | {url}, depth=depth + 1)
        elif playback["format"] == "mp4":
            media_url = url
        else:
            return {**evidence, "verified": False, "reason": "unsupported_format"}
        with requests.get(media_url, headers={**headers, "Range": "bytes=0-1023"},
                          timeout=timeout, stream=True) as response:
            response.raise_for_status()
            head = next(response.iter_content(1024), b"")
            ts = len(head) >= 377 and all(head[i] == 0x47 for i in (0, 188, 376))
            mp4 = len(head) >= 8 and head[4:8] in {b"ftyp", b"styp", b"moof", b"moov"}
            return {**evidence, "verified": ts or mp4, "segment_status": response.status_code,
                    "sample_bytes": len(head), "mpeg_ts_sync": ts, "mp4_box": mp4,
                    "segment_content_type": response.headers.get("Content-Type"),
                    "segment_content_range": response.headers.get("Content-Range")}
