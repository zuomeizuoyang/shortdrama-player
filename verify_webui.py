"""Verify live six-provider API paths and the local media relay."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urljoin

import requests


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--url",default="http://127.0.0.1:8790")
    parser.add_argument("--output",default=str(Path(__file__).with_name("webui_verification.json")))
    args=parser.parse_args()
    client=requests.Session()
    base=args.url.rstrip("/")
    def call(platform,operation,**data):
        response=client.post(base+"/api/"+platform+"/"+operation,json={"language":"en",**data},timeout=120)
        response.raise_for_status();result=response.json();assert result["ok"];return result["data"]
    evidence={"checked_at":datetime.now(timezone.utc).isoformat(),"version":"0.2.0","results":{},"scope":"live six-provider API + local media headers; public accessible playback only"}
    for platform in ["dramabox","reelshort","dramawave","netshort","goodshort"]:
        print(platform+": API chain",flush=True)
        try:
            feed=call(platform,"feed")
            assert feed["items"]
            extra=call(platform,"feed",cursor=feed["next_cursor"]) if feed["next_cursor"] else None
            keyword="CEO" if platform=="dramawave" else "love"
            search=call(platform,"search",keyword=keyword)
            assert search["items"]
            drama_id=feed["items"][0]["drama_id"]
            detail=call(platform,"detail",drama_id=drama_id)
            chapters=call(platform,"episodes",drama_id=drama_id)["items"]
            playable=next(row for row in chapters if not row["locked"] and row.get("index",0)>0 and not row.get("is_blooper"))
            playback=call(platform,"resolvePlayback",episode_id=playable["episode_id"])
            assert playback["status"]=="playable" and playback["url"].startswith("/media/") and playback["headers"]=={}
            url=urljoin(base,playback["url"])
            manifests=[]
            if playback["format"]=="hls":
                for _ in range(4):
                    manifest=client.get(url,timeout=45);manifest.raise_for_status()
                    text=manifest.text;assert text.startswith("#EXTM3U")
                    manifests.append(manifest.status_code)
                    lines=[line for line in text.splitlines() if line and not line.startswith("#")]
                    assert lines and lines[0].startswith("/media/")
                    url=urljoin(base,lines[0])
                    if "#EXT-X-STREAM-INF" not in text:break
            with client.get(url,headers={"Range":"bytes=0-1023"},timeout=45,stream=True) as media:
                media.raise_for_status();head=next(media.iter_content(1024),b"")
                valid=len(head)>=8 and head[4:8] in {b"ftyp",b"styp",b"moof",b"moov"} or len(head)>=377 and all(head[i]==0x47 for i in [0,188,376])
                assert valid
                probe={"status":media.status_code,"bytes":len(head),"container_valid":valid,"range":media.headers.get("Content-Range")}
            locked=next((row for row in reversed(chapters) if row["locked"]),None)
            if locked:
                blocked=call(platform,"resolvePlayback",episode_id=locked["episode_id"])
                assert blocked["status"]=="locked" and blocked["url"] is None
            evidence["results"][platform]={"passed":True,"feed_counts":[len(feed["items"]),len(extra["items"]) if extra else None],"search_keyword":keyword,"search_count":len(search["items"]),"drama_id":drama_id,"title":detail["title"],"chapter_count":len(chapters),"format":playback["format"],"manifest_statuses":manifests,"local_relay":probe,"locked_verified":bool(locked)}
        except Exception as exc:
            evidence["results"][platform]={"passed":False,"error_type":type(exc).__name__,"error":str(exc).split(" for url:")[0]}
        print(platform+": "+("PASS" if evidence["results"][platform]["passed"] else "FAIL"),flush=True)
        Path(args.output).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf8")
    print("hongguo: API chain",flush=True)
    try:
        from verify_hongguo import verify
        evidence["results"]["hongguo"]=verify(base)
    except Exception as exc:
        evidence["results"]["hongguo"]={"passed":False,"error_type":type(exc).__name__,"error":str(exc).split(" for url:")[0]}
    print("hongguo: "+("PASS" if evidence["results"]["hongguo"]["passed"] else "FAIL"),flush=True)
    Path(args.output).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf8")
    return 0 if all(row["passed"] for row in evidence["results"].values()) else 1


if __name__=="__main__":raise SystemExit(main())
