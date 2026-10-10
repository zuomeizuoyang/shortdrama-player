"""Local HTTP integration and Hongguo contract checks; no external requests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

import requests

from app import AppState, MediaStore, build_server
from hongguo import HongguoAdapter
from overseas.common import AdapterError
from providers import ProviderManager, episode


class HongguoFixture(HongguoAdapter):
    def __init__(self):
        super().__init__("https://backend.example", "test-only-secret")
        self.calls = []
        self.ciphertext_only = False

    def _api(self, path, params):
        self.calls.append((path, params))
        book = {"series_id": "123", "title": "示例剧", "episode_cnt": 3, "cover": ""}
        if path == "/rank":
            offset, limit = int(params["offset"]), int(params["limit"])
            rows = [{**book, "series_id": str(i)} for i in range(1, 30)]
            end = offset + limit
            return {"items": rows[offset:end], "has_more": end < len(rows),
                    "next_offset": end if end < len(rows) else None}
        if path == "/browse":
            return {"items": [{**book, "series_id": str(i)} for i in range(1, 30)]}
        if path == "/search":
            return {"results": [book]}
        if path == "/episodes":
            return {"meta": book, "episodes": [{"index": i, "vid": str(100+i), "title": str(i), "locked": i == 3} for i in range(1,4)]}
        if path == "/play":
            return {"episodes": [{"index": int(params["ep"]), "encrypted_url": "https://cipher.example/video", **({} if self.ciphertext_only else {"stream_url": "/stream?vid=101"})}]}
        raise AssertionError(path)


class HongguoContract(unittest.TestCase):
    def test_five_methods_and_portable_episode_identity(self):
        client = HongguoFixture()
        first = client.feed()
        second = client.feed(first["next_cursor"])
        self.assertEqual(len(first["items"]),20)
        self.assertEqual(len(second["items"]),9)
        self.assertFalse(second["has_more"])
        self.assertEqual(client.search("剧")["items"][0]["drama_id"],"123")
        self.assertEqual(client.detail("123")["episode_count"],3)
        chapters = client.episodes("123")
        playback = HongguoFixture().resolvePlayback(chapters[0]["episode_id"])
        self.assertEqual(playback["status"],"playable")
        self.assertEqual(playback["headers"]["X-API-Key"],"test-only-secret")

    def test_locked_does_not_call_play_or_expose_stream(self):
        client=HongguoFixture()
        result=client.resolvePlayback("hongguo:123:3")
        self.assertEqual(result["status"],"locked")
        self.assertIsNone(result["url"])
        self.assertNotIn("/play",[path for path,_ in client.calls])

    def test_ciphertext_is_not_a_playback_url(self):
        client=HongguoFixture();client.ciphertext_only=True
        result=client.resolvePlayback("hongguo:123:1")
        self.assertEqual(result["status"],"official_player_required")
        self.assertIsNone(result["url"])

    def test_missing_configuration_and_invalid_episode(self):
        with self.assertRaises(AdapterError): HongguoAdapter().feed()
        with self.assertRaises(ValueError): HongguoFixture().resolvePlayback("hongguo:123:9")
        with self.assertRaises(ValueError): HongguoAdapter.validate_base_url("https://host/?api_key=secret")

    def test_complete_chapter_count_is_required(self):
        client=HongguoFixture()
        original=client._api
        def fewer(path,params):
            raw=original(path,params)
            if path=="/episodes":raw["episodes"].pop()
            return raw
        client._api=fewer
        with self.assertRaises(AdapterError): client.episodes("123")


class MediaAndRegistry(unittest.TestCase):
    def test_master_audio_map_and_relative_media_are_rewritten(self):
        store=MediaStore(); resource={"headers":{"Referer":"https://official.example"},"private":False}
        text='#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio/audio.m3u8"\n#EXT-X-MAP:URI="init.mp4"\n#EXT-X-STREAM-INF:BANDWIDTH=100\nvideo/index.m3u8\n'
        rewritten=store.rewrite_manifest(text,"https://cdn.example/master.m3u8","sid",resource)
        self.assertNotIn('URI="audio/',rewritten)
        targets=[store.get(path.split("/")[-1],"sid")["url"] for path in re.findall(r'/media/[A-Za-z0-9_-]+',rewritten)]
        self.assertEqual(targets,["https://cdn.example/audio/audio.m3u8","https://cdn.example/init.mp4","https://cdn.example/video/index.m3u8"])
        self.assertEqual(store.get(re.findall(r'/media/[A-Za-z0-9_-]+',rewritten)[0].split("/")[-1],"sid")["kind"],"hls")

    def test_encrypted_manifest_never_registers_key(self):
        store=MediaStore()
        with self.assertRaises(AdapterError):store.rewrite_manifest('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\nvideo.ts',"https://cdn.example/master.m3u8","sid",{"headers":{},"private":False})
        self.assertFalse(store.items)

    def test_media_tokens_are_session_bound_and_headers_stay_server_side(self):
        state=AppState()
        result=state.decorate({"platform":"hongguo","status":"playable","url":"http://127.0.0.1:8000/stream?vid=1","headers":{"X-API-Key":"test-only-secret"},"format":"mp4"},"sid")
        self.assertNotIn("test-only-secret",json.dumps(result))
        self.assertTrue(result["url"].startswith("/media/"))
        token=result["url"].split("/")[-1]
        with self.assertRaises(ValueError):state.media.get(token,"other")
        self.assertEqual(state.media.get(token,"sid")["headers"]["X-API-Key"],"test-only-secret")

    def test_six_clients_and_normalized_access(self):
        manager=ProviderManager();_,session=manager.get_session()
        with session.lock:
            for platform in ("reelshort","dramabox","dramawave","netshort","goodshort","hongguo"):
                self.assertTrue(hasattr(manager.client(session,platform,"zh-CN" if platform=="hongguo" else "en"),"resolvePlayback"))
        self.assertEqual(len(manager.platform_info()),6)
        self.assertTrue(episode({"access":"locked"},"reelshort")["locked"])
        self.assertFalse(episode({"unlock":None,"access":"unknown"},"dramawave")["locked"])
        manager.close()


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append({"path":self.path,"range":self.headers.get("Range"),"key":self.headers.get("X-Test-Key")})
        if self.path=="/video.mp4":
            body=b'\x00\x00\x00\x18ftypisomtestdata'
            match=re.fullmatch(r"bytes=(\d+)-(\d+)",self.headers.get("Range", ""))
            self.send_response(206 if match else 200)
            if match:
                start,end=map(int,match.groups());total=len(body);body=body[start:end+1]
                self.send_header("Content-Range",f"bytes {start}-{min(end,total-1)}/{total}")
            self.send_header("Content-Type","video/mp4")
            self.send_header("Content-Length",str(len(body)));self.send_header("Accept-Ranges","bytes")
            self.end_headers();self.wfile.write(body)
        else:
            self.send_response(404);self.send_header("Content-Length","0");self.end_headers()
    def log_message(self,*args):pass


class LocalHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=build_server(port=0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.server.server_port}"
        cls.upstream=ThreadingHTTPServer(("127.0.0.1",0),Upstream);cls.upstream.requests=[]
        cls.upthread=threading.Thread(target=cls.upstream.serve_forever,daemon=True);cls.upthread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.server.state.providers.close()
        cls.upstream.shutdown();cls.upstream.server_close()

    def test_static_platforms_config_and_missing_hongguo(self):
        client=requests.Session()
        self.assertIn("ShortDrama",client.get(self.base+"/").text)
        self.assertEqual(len(client.get(self.base+"/api/platforms").json()["data"]),6)
        response=client.post(self.base+"/api/hongguo/feed",json={"language":"zh-CN"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()["error"]["code"],"configuration_required")
        self.assertNotIn("api_key",client.get(self.base+"/api/config").json()["data"])
        self.assertEqual(client.get(self.base+"/../app.py").status_code,404)

    def test_range_relay_keeps_status_and_service_headers(self):
        client=requests.Session();client.get(self.base+"/api/health")
        sid=client.cookies.get("shortdrama_session")
        url=f"http://127.0.0.1:{self.upstream.server_port}/video.mp4"
        path=self.server.state.media.register(sid,url,{"X-Test-Key":"test-only-secret"},private=True)
        response=client.get(self.base+path,headers={"Range":"bytes=0-7"})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.content,b'\x00\x00\x00\x18ftyp')
        self.assertTrue(response.headers["Content-Range"].startswith("bytes 0-7/"))
        self.assertEqual(self.upstream.requests[-1]["range"],"bytes=0-7")
        self.assertEqual(self.upstream.requests[-1]["key"],"test-only-secret")
        self.assertNotIn("X-Test-Key",response.headers)
        self.assertEqual(requests.get(self.base+path).status_code,400)

    def test_cross_origin_settings_are_rejected(self):
        response=requests.post(self.base+"/api/config",json={"hongguo_base_url":"https://backend.example"},headers={"Origin":"https://other.example"})
        self.assertEqual(response.status_code,403)

    def test_five_json_routes_with_configured_backend_contract(self):
        client=requests.Session();client.get(self.base+"/api/health")
        _,session=self.server.state.providers.get_session(client.cookies.get("shortdrama_session"))
        session.clients[("hongguo","zh-CN")]=HongguoFixture()
        for op,payload in [("feed",{}),("search",{"keyword":"剧"}),("detail",{"drama_id":"123"}),("episodes",{"drama_id":"123"}),("resolvePlayback",{"episode_id":"hongguo:123:1"})]:
            response=client.post(self.base+"/api/hongguo/"+op,json={"language":"zh-CN",**payload})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()["ok"])
            self.assertNotIn("test-only-secret",response.text)


if __name__=="__main__":unittest.main()
