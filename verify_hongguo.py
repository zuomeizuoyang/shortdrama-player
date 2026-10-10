"""Live check of Hongguo public-web backend through the unified Web UI API."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import requests


def verify(base):
    with requests.Session() as client:
        def api(operation, **fields):
            response = client.post(base + "/api/hongguo/" + operation,
                                   json={"language": "zh-CN", **fields}, timeout=120)
            response.raise_for_status()
            value = response.json()
            assert value.get("ok"), value
            return value["data"]

        config = client.get(base + "/api/config", timeout=10).json()["data"]
        assert config["configured"]
        first = api("feed")
        assert first["items"]
        counts = [len(first["items"])]
        if first.get("next_cursor"):
            counts.append(len(api("feed", cursor=first["next_cursor"])["items"]))
        search = api("search", keyword="皇后")
        assert search["items"]
        # Use an explicit sample: recommendation ordering changes daily.
        sid = "7687919221593885758"
        assert any(row["drama_id"] == sid for row in first["items"]), "Sample no longer in first recommendation page"
        detail = api("detail", drama_id=sid)
        chapters = api("episodes", drama_id=sid)["items"]
        assert len(chapters) == int(detail["episode_count"])
        free = next(row for row in chapters if row["access"] == "free")
        playback = api("resolvePlayback", episode_id=free["episode_id"])
        assert playback["status"] == "playable" and playback["format"] == "mp4"
        assert playback["url"].startswith("/media/")
        with client.get(base + playback["url"], headers={"Range": "bytes=0-1023"}, timeout=45, stream=True) as stream:
            stream.raise_for_status()
            head = stream.raw.read(1024)
            assert stream.status_code == 206 and len(head) == 1024 and b"ftyp" in head[:32]
            probe = {"status": stream.status_code, "bytes": len(head), "container_valid": True,
                     "range": stream.headers.get("Content-Range"), "type": stream.headers.get("Content-Type")}
        restricted = next((r for r in chapters if r.get("official_only")), None)
        official = api("resolvePlayback", episode_id=restricted["episode_id"]) if restricted else None
        if official:
            assert official["status"] == "official_player_required" and official["url"] is None
        return {"passed": True, "live_verified": True, "backend_type": config["backend_type"],
                "backend_address": config["hongguo_base_url"], "feed_counts": counts,
                "search_keyword": "皇后", "search_count": len(search["items"]),
                "search_total_reported": search.get("total"), "search_pagination": search.get("pagination"),
                "drama_id": sid, "title": detail["title"], "chapter_count": len(chapters),
                "web_accessible_episode_count": detail.get("web_accessible_episode_count"),
                "format": playback["format"], "local_relay": probe,
                "official_app_episode_status": official["status"] if official else None,
                "official_app_episode_has_url": bool(official and official["url"]),
                "playback_scope": "Only episodes made accessible by the official public website",
                "source": "public catalog + official website SSR; no native DRM backend"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8790")
    args = parser.parse_args()
    result = verify(args.base.rstrip("/"))
    record = {"checked_at": datetime.now(timezone.utc).isoformat(), "result": result}
    path = Path(__file__).with_name("hongguo_verification.json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
