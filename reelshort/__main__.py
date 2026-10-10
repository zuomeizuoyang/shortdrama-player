from __future__ import annotations

import argparse
import json
import sys

import requests

from . import ReelShortAdapter, ReelShortError


def main():
    parser = argparse.ArgumentParser(description="ReelShort public website adapter")
    parser.add_argument("--language", default="en")
    commands = parser.add_subparsers(dest="command", required=True)
    feed = commands.add_parser("feed")
    feed.add_argument("--cursor")
    feed.add_argument("--pages", type=int, default=1,
                      help="Read consecutive pages in the same snapshot client (1-50)")
    search = commands.add_parser("search")
    search.add_argument("keyword")
    search.add_argument("--cursor")
    detail = commands.add_parser("detail")
    detail.add_argument("drama_id")
    episodes = commands.add_parser("episodes")
    episodes.add_argument("drama_id")
    play = commands.add_parser("play")
    play.add_argument("episode_id", help="drama_id:chapter_id")
    play.add_argument("--verify", action="store_true", help="Probe manifest and a small media range")
    args = parser.parse_args()
    client = ReelShortAdapter()
    try:
        if args.command == "feed":
            if not 1 <= args.pages <= 50:
                raise ValueError("--pages must be between 1 and 50")
            result = client.feed(cursor=args.cursor, language=args.language)
            if args.pages > 1:
                pages = [result]
                while len(pages) < args.pages and pages[-1].get("next_cursor"):
                    pages.append(client.feed(cursor=pages[-1]["next_cursor"],
                                             language=args.language))
                result = {"pages": pages, "page_count": len(pages)}
        elif args.command == "search":
            result = client.search(args.keyword, language=args.language, cursor=args.cursor)
        elif args.command == "detail":
            result = client.detail(args.drama_id, language=args.language)
        elif args.command == "episodes":
            result = client.episodes(args.drama_id, language=args.language)
        else:
            result = client.resolvePlayback(args.episode_id, language=args.language)
            if args.verify:
                result["verification"] = client.verify_stream(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReelShortError, ValueError, requests.RequestException) as error:
        print(json.dumps({"error": str(error), "code": getattr(error, "code", None)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        client.close()

if __name__ == "__main__":
    raise SystemExit(main())
