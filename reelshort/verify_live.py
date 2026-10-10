"""Live acceptance check; does not purchase chapters or fetch full videos.

Run from the directory containing this package: python -m reelshort.verify_live
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from . import ReelShortAdapter


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    client = ReelShortAdapter()
    evidence = {"checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "passed": False, "feed": {}, "search": {}, "dramas": []}
    try:
        feed_items = {}
        for language in ("en", "es"):
            pages, items, cursor = [], [], None
            for _ in range(20):
                page = client.feed(cursor=cursor, language=language)
                check(page["has_more"] == bool(page["next_cursor"]), "Inconsistent feed cursor")
                pages.append({"count": len(page["items"]), "source": page["source"],
                              "raw_item_count": page.get("raw_item_count"),
                              "end_reason": page.get("end_reason")})
                items.extend(page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            check(cursor is None, "Feed did not finish within the acceptance limit")
            ids = [item["drama_id"] for item in items]
            check(items and len(ids) == len(set(ids)), "Feed is empty or contains duplicate IDs")
            check(any(page["source"] == "official_web_waterfall_api" for page in pages),
                  "Official feed continuation was not exercised")
            feed_items[language] = items
            evidence["feed"][language] = {"unique_count": len(items), "pages": pages,
                                           "finished": True}

        for language, keyword in (("en", "love"), ("es", "amor")):
            first = client.search(keyword, language=language)
            check(first["items"] and first["next_cursor"], "Search first page lacks results/cursor")
            second = client.search(keyword, cursor=first["next_cursor"], language=language)
            check(second["items"] and second["page"] == 2, "Search continuation failed")
            first_ids = {item["drama_id"] for item in first["items"]}
            second_ids = {item["drama_id"] for item in second["items"]}
            check(first_ids != second_ids, "Search page 2 repeats page 1")
            evidence["search"][language] = {"keyword": keyword, "total": first["total"],
                                              "first_count": len(first_ids),
                                              "second_count": len(second_ids),
                                              "page_two_differs": True}

        # Connect a current feed item to search/detail/playback, then verify two
        # established examples independently of changes to homepage rankings.
        feed_sample = feed_items["en"][0]
        targets = [(feed_sample["title"], feed_sample["drama_id"]),
                   ("genie", None), ("In Bed with My Brother-in-Law", None)]
        for keyword, expected_id in targets:
            results = client.search(keyword)
            check(results["items"], "Verification title is absent from live search")
            if expected_id:
                check(any(row["drama_id"] == expected_id for row in results["items"]),
                      "Current feed sample was not found by its title in search")
            book_id = expected_id or results["items"][0]["drama_id"]
            detail = client.detail(book_id)
            chapters = client.episodes(book_id)
            check(detail["drama_id"] == book_id, "Detail ID mismatch")
            check(chapters and len(chapters) == detail["episode_count"], "Incomplete chapter list")
            chapter_ids = [row["chapter_id"] for row in chapters]
            check(len(chapter_ids) == len(set(chapter_ids)), "Duplicate chapters")
            numbers = [row["number"] for row in chapters if row["number"] != 0]
            check(numbers == list(range(1, max(numbers) + 1)), "Non-contiguous episode numbers")
            free = next(row for row in chapters if row["access"] == "free")
            locked = next(row for row in chapters if row["access"] == "locked")
            playback = client.resolvePlayback(free["episode_id"])
            check(playback["status"] == "playable" and playback["url"], "Free playback failed")
            verification = client.verify_stream(playback)
            check(verification["verified"], "Free media bytes were not validated")
            before = len(client.request_log)
            restricted = client.resolvePlayback(locked["episode_id"])
            check(restricted["status"] == "locked" and restricted["url"] is None,
                  "Locked episode was not represented accurately")
            restricted_reads = client.request_log[before:]
            check(not any(row["path"].startswith("/episodes/") for row in restricted_reads),
                  "Locked episode page should not have been fetched")
            evidence["dramas"].append({
                "drama_id": book_id, "title": detail["title"],
                "present_in_feed": any(row["drama_id"] == book_id for row in feed_items["en"]),
                "episode_count": len(chapters),
                "access_counts": dict(Counter(row["access"] for row in chapters)),
                "free_episode_id": free["episode_id"], "playback_status": playback["status"],
                "media_verification": verification,
                "locked_episode_id": locked["episode_id"], "locked_status": restricted["status"],
                "locked_url_returned": False,
            })
        check(any(row["present_in_feed"] for row in evidence["dramas"]),
              "No verified playback title was connected to the live feed")
        check(not any("getChapterContent" in row["path"] for row in client.request_log),
              "Acceptance must not invoke the chapter purchase/content action")
        evidence["requests"] = client.request_log
        evidence["passed"] = True
        target = Path(__file__).with_name("verification.json")
        target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "evidence": str(target.resolve()),
                          "feed_counts": {key: value["unique_count"] for key, value in evidence["feed"].items()},
                          "dramas": [{"title": row["title"], "episodes": row["episode_count"],
                                      "access_counts": row["access_counts"],
                                      "stream_verified": row["media_verification"]["verified"]}
                                     for row in evidence["dramas"]]}, ensure_ascii=False, indent=2))
        return evidence
    finally:
        client.close()


if __name__ == "__main__":
    run()
