"""Run current official-site checks; evidence omits temporary stream URLs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from . import ADAPTERS, create_adapter


def locked(episode):
    if "locked" in episode:
        return episode["locked"]
    return episode.get("access") == "locked"


def page_summary(page):
    items = page["items"]
    ids = [row["drama_id"] for row in items]
    assert len(ids) == len(set(ids)), "Duplicate cards in page"
    assert page["has_more"] == bool(page["next_cursor"]), "Inconsistent cursor end"
    return {"count": len(items), "has_more": page["has_more"],
            "source": page.get("source"), "snapshot_total": page.get("snapshot_total")}


def verify_platform(platform, language="en"):
    client = create_adapter(platform, timeout=30)
    try:
        print(platform + ": feed/search", flush=True)
        feed = client.feed(language=language)
        assert feed["items"], "Empty first recommendation page"
        result = {"platform": platform, "language": language, "feed": [page_summary(feed)]}
        if feed["next_cursor"]:
            second = client.feed(feed["next_cursor"], language=language)
            assert not ({r["drama_id"] for r in feed["items"]} & {r["drama_id"] for r in second["items"]})
            result["feed"].append(page_summary(second))
        keyword = "CEO" if platform == "dramawave" else "love"
        search = client.search(keyword, language=language)
        assert search["items"], "Known keyword returned no matches"
        result["search"] = {"keyword": keyword, "pages": [page_summary(search)]}
        if search["next_cursor"]:
            second = client.search(keyword, language=language, cursor=search["next_cursor"])
            assert second["items"], "Search page two returned no additional matches"
            assert not ({r["drama_id"] for r in search["items"]} & {r["drama_id"] for r in second["items"]})
            result["search"]["pages"].append(page_summary(second))
        samples = []
        for card in (feed["items"][0], search["items"][0]):
            if any(row["drama_id"] == card["drama_id"] for row in samples):
                continue
            drama_id = card["drama_id"]
            print(platform + ": detail/episodes " + drama_id, flush=True)
            detail = client.detail(drama_id, language=language)
            episodes = client.episodes(drama_id, language=language)
            assert str(detail["drama_id"]) == str(drama_id)
            assert episodes, "No episodes"
            main_episodes = [ep for ep in episodes if ep.get("is_blooper") is not True]
            expected = detail.get("episode_count")
            if expected is not None:
                assert int(expected) == len(main_episodes), "Incomplete main episode list"
            indices = [ep["index"] for ep in episodes]
            assert indices == list(range(1, len(episodes) + 1)), "Episode order is incomplete"
            assert len({ep["episode_id"] for ep in episodes}) == len(episodes)
            playback = client.resolvePlayback(episodes[0]["episode_id"], language=language)
            assert playback["status"] == "playable", "First episode is not publicly playable"
            media = client.verify_stream(playback)
            assert media["verified"], "Media probe failed"
            restricted = next((ep for ep in reversed(episodes) if locked(ep)), None)
            restricted_result = None
            if restricted:
                response = client.resolvePlayback(restricted["episode_id"], language=language)
                assert response["status"] == "locked" and response.get("url") is None
                restricted_result = {"episode_id": restricted["episode_id"], "status": response["status"],
                                     "url_present": False}
            samples.append({"drama_id": drama_id, "title": detail["title"],
                            "episode_count": len(main_episodes), "episode_count_returned": len(episodes),
                            "bonus_episode_count": len(episodes) - len(main_episodes),
                            "locked_count": sum(bool(locked(ep)) for ep in episodes),
                            "first_episode_id": episodes[0]["episode_id"], "playback_status": playback["status"],
                            "format": playback["format"], "media": media, "restricted": restricted_result})
        result.update(samples=samples, requests=client.request_log, passed=True,
                      checked_at=datetime.now(timezone.utc).isoformat())
        return result
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("platforms", nargs="*", choices=list(ADAPTERS))
    parser.add_argument("--language", default="en")
    parser.add_argument("--output", default=str(Path(__file__).with_name("verification.json")))
    args = parser.parse_args()
    path = Path(args.output)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"results": {}}
    for platform in args.platforms or ADAPTERS:
        try:
            result = verify_platform(platform, args.language)
        except Exception as exc:
            result = {"platform": platform, "language": args.language, "passed": False,
                      "error_type": type(exc).__name__, "error": str(exc),
                      "checked_at": datetime.now(timezone.utc).isoformat()}
        existing["results"][platform] = result
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        print(platform + ": " + ("PASS" if result["passed"] else "FAIL " + result["error"]), flush=True)
    return 0 if all(existing["results"][p]["passed"] for p in args.platforms or ADAPTERS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
