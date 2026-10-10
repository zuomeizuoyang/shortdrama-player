"""ReelShort playback through official pages, without purchasing chapters.

The anonymous episode SSR can include video_url even for paid episodes. A URL
is therefore never treated as entitlement: paid_start and chapter access are
checked before returning a stream. getChapterContent may debit coins, so this
adapter deliberately does not call it to resolve playback.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

import requests


def _page_data(html: str) -> dict:
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("Official page has no NEXT_DATA")
    page = json.loads(match.group(1))
    return page.get("props", {}).get("pageProps", {})


def _integer(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class PlaybackMixin:
    def resolvePlayback(self, episode_id: str, language: str = "en") -> dict:
        """Return a stream or an explicit access state for drama_id:chapter_id.

        Paid chapters are delegated to the official player. Existing account
        entitlements are not inferred from a URL or a visitor session.
        """
        match = re.fullmatch(r"([A-Za-z0-9]+):([A-Za-z0-9]+)", str(episode_id))
        if not match:
            raise ValueError("episode_id must be drama_id:chapter_id")
        drama_id, chapter_id = match.groups()
        base = {
            "episode_id": episode_id, "drama_id": drama_id,
            "chapter_id": chapter_id, "platform": "reelshort",
            "status": "official_page", "url": None, "format": None,
            "headers": {}, "language": language, "expires_at": None,
            "official_url": f"https://www.reelshort.com/movie/{drama_id}",
        }
        try:
            chapters = self.episodes(drama_id, language=language)
            if isinstance(chapters, dict):
                chapters = chapters.get("items", chapters.get("episodes", []))
            chapter = next((item for item in chapters
                            if str(item.get("chapter_id", item.get("id"))) == chapter_id), None)
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
            base["reason"] = "chapter_metadata_unavailable"
            base["error_type"] = type(exc).__name__
            return base
        if chapter is None:
            return {**base, "status": "not_found", "reason": "unknown_chapter"}
        number = _integer(chapter.get("number", chapter.get("index", chapter.get("serial_number"))))
        path = f"/episodes/episode-{number}-show-{drama_id}-{chapter_id}"
        official_url = chapter.get("official_url") or f"https://www.reelshort.com{path}"
        parsed = urlsplit(official_url)
        if parsed.hostname not in {"www.reelshort.com", "reelshort.com"}:
            raise ValueError("episode official_url must belong to ReelShort")
        base.update(official_url=official_url, number=number)
        access_flag = _integer(chapter.get("is_lock"), -1)
        if chapter.get("access") == "locked" or access_flag == 1:
            return {**base, "status": "locked", "reason": "chapter_access_locked"}
        try:
            props = _page_data(self.get_html(parsed.path, language=language))
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            return {**base, "reason": "official_page_unavailable", "error_type": type(exc).__name__}
        if props.get("code") in {101, 403}:
            return {**base, "status": "login_required", "reason": "official_authentication_required"}
        data = props.get("data", {})
        if str(data.get("book_id")) != drama_id or str(data.get("chapter_id")) != chapter_id:
            return {**base, "status": "not_found", "reason": "episode_page_mismatch"}
        if props.get("web_prohibit") or _integer(data.get("copyright_restrictions")) == 1:
            return {**base, "reason": "official_player_restriction"}
        serial = _integer(data.get("serial_number"), number)
        paid_start = _integer(data.get("paid_start"))
        # Follow the official player's paid-episode predicate. SSR URLs are
        # excluded for those chapters even if the HTML happens to contain one.
        if (_integer(data.get("is_lock"), -1) == 1
                or (access_flag not in {0, 2} and (_integer(chapter.get("vip_free")) == 1
                    or _integer(data.get("vip_free")) == 1
                    or (paid_start and serial >= paid_start)))):
            return {**base, "status": "locked", "reason": "paid_episode_requires_official_entitlement",
                    "paid_start": paid_start}
        if access_flag not in {0, 2}:
            free_boundary = (paid_start > 0 and serial < paid_start
                             and _integer(chapter.get("vip_free")) != 1
                             and _integer(data.get("vip_free")) != 1)
            explicitly_free_drama = _integer(data.get("is_paid"), -1) == 0
            if not (explicitly_free_drama or free_boundary):
                return {**base, "reason": "access_unconfirmed", "paid_start": paid_start}
        url = data.get("video_url")
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
            return {**base, "reason": "no_public_stream"}
        suffix = urlsplit(url).path.lower()
        media_format = "hls" if suffix.endswith(".m3u8") else "mp4" if suffix.endswith(".mp4") else "unknown"
        if media_format == "unknown":
            return {**base, "reason": "official_player_format"}
        return {**base, "status": "playable", "url": url, "format": media_format,
                "duration": data.get("duration"), "source": "official_episode_ssr",
                "headers": {"Referer": official_url}, "paid_start": paid_start}

    def verify_stream(self, playback: dict) -> dict:
        """Fetch a manifest and 1 KiB of its first media segment, or MP4 range.

        A key declaration causes an official-player fallback; this method never
        retrieves a key or decrypts media. No stream URL is copied into evidence.
        """
        return self._verify_stream(playback, seen=frozenset(), depth=0)

    def _verify_stream(self, playback: dict, *, seen: frozenset, depth: int) -> dict:
        if playback.get("status") != "playable":
            return {"verified": False, "reason": "not_playable", "status": playback.get("status")}
        headers = dict(playback.get("headers") or {})
        url = playback["url"]
        timeout = getattr(self, "timeout", 30)
        if playback["format"] == "hls":
            if url in seen:
                return {"verified": False, "reason": "hls_manifest_cycle"}
            if depth >= 3:
                return {"verified": False, "reason": "hls_manifest_depth_limit"}
            seen = seen | {url}
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            text = response.text
            evidence = {"format": "hls", "manifest_status": response.status_code,
                        "manifest_content_type": response.headers.get("Content-Type"),
                        "manifest_bytes": len(response.content), "cdn_host": urlsplit(url).hostname}
            if not text.lstrip().startswith("#EXTM3U"):
                return {**evidence, "verified": False, "reason": "invalid_hls_manifest"}
            # Also refrain from accessing ordinary AES keys; an official
            # playback page can handle such streams for the user.
            if re.search(r"#EXT-X-(?:SESSION-)?KEY:.*METHOD=(?!NONE)", text):
                return {**evidence, "verified": False, "reason": "encrypted_media_official_player_required"}
            media_lines = [line.strip() for line in text.splitlines()
                           if line.strip() and not line.startswith("#")]
            if not media_lines:
                return {**evidence, "verified": False, "reason": "empty_hls_manifest"}
            segment_url = urljoin(url, media_lines[0])
            if "#EXT-X-STREAM-INF" in text:
                nested = {**playback, "url": segment_url}
                return {**self._verify_stream(nested, seen=seen, depth=depth + 1),
                        "master_manifest_status": response.status_code}
        else:
            evidence = {"format": "mp4", "cdn_host": urlsplit(url).hostname}
            segment_url = url
        with requests.get(segment_url, headers={**headers, "Range": "bytes=0-1023"},
                          timeout=timeout, stream=True) as response:
            response.raise_for_status()
            head = next(response.iter_content(1024), b"")
            ts = len(head) >= 377 and all(head[i] == 0x47 for i in (0, 188, 376))
            mp4 = len(head) >= 8 and head[4:8] in {b"ftyp", b"styp", b"moof", b"moov"}
            return {**evidence, "verified": ts or mp4,
                    "segment_status": response.status_code,
                    "segment_content_type": response.headers.get("Content-Type"),
                    "segment_content_range": response.headers.get("Content-Range"),
                    "sample_bytes": len(head), "mpeg_ts_sync": ts, "mp4_box": mp4}
