"""Local six-platform Web UI, JSON API and authorized media relay."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urljoin, urlsplit
import webbrowser

import requests

from overseas.common import AdapterError
from providers import PLATFORM_IDS, ProviderManager

ROOT = Path(__file__).resolve().parent
VERSION = "0.2.0"


class MediaStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.items = {}
        self.lookup = {}

    def register(self, sid, url, headers=None, kind="binary", private=False):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("资源地址无效")
        if not private:
            if parsed.hostname.lower() == "localhost":
                raise ValueError("无效的媒体来源")
            try:
                if not ipaddress.ip_address(parsed.hostname).is_global:
                    raise ValueError("无效的媒体来源")
            except ValueError as exc:
                if str(exc) == "无效的媒体来源":
                    raise
        headers = dict(headers or {})
        key = (sid, url, kind, tuple(sorted(headers.items())))
        with self.lock:
            now = time.monotonic()
            for token in [t for t, row in self.items.items() if now - row["created"] > 7200]:
                old = self.items.pop(token)
                self.lookup.pop(old["key"], None)
            token = self.lookup.get(key)
            if token:
                self.items[token]["created"] = now
            else:
                if len(self.items) >= 8000:
                    raise RuntimeError("资源会话已满，请重新启动服务")
                token = secrets.token_urlsafe(24)
                self.items[token] = {"sid": sid, "url": url, "headers": headers, "kind": kind,
                                     "created": now, "private": private, "key": key}
                self.lookup[key] = token
        return "/media/" + token

    def get(self, token, sid=None):
        with self.lock:
            row = self.items.get(token)
            if not row or row["sid"] != sid or time.monotonic() - row["created"] > 7200:
                raise ValueError("媒体会话已过期，请重新选择单集")
            row["created"] = time.monotonic()
            return row.copy()

    def rewrite_manifest(self, text, base_url, sid, resource):
        if not text.lstrip().startswith("#EXTM3U"):
            raise ValueError("播放地址没有返回 HLS 清单")
        if re.search(r"#EXT-X-(?:SESSION-)?KEY:.*METHOD=(?!NONE)", text):
            raise AdapterError("该视频需要官方播放器", code="official_player_required")

        def register_uri(uri):
            target = urljoin(base_url, uri)
            kind = "hls" if urlsplit(target).path.lower().endswith(".m3u8") else "binary"
            return self.register(sid, target, resource["headers"], kind, resource["private"])

        lines = []
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                lines.append(register_uri(line.strip()))
            else:
                lines.append(re.sub(r'URI="([^"]+)"', lambda match: 'URI="' + register_uri(match.group(1)) + '"', line))
        return "\n".join(lines) + "\n"


class AppState:
    def __init__(self):
        self.providers = ProviderManager()
        self.media = MediaStore()

    def decorate(self, value, sid):
        if isinstance(value, list):
            return [self.decorate(row, sid) for row in value]
        if not isinstance(value, dict):
            return value
        output = {key: self.decorate(row, sid) for key, row in value.items()}
        if output.get("cover"):
            try:
                output["cover_source"] = output["cover"]
                output["cover"] = self.media.register(sid, output["cover"], kind="image")
            except ValueError:
                output["cover"] = ""
        if output.get("status") == "playable" and output.get("url"):
            url, headers = output["url"], output.get("headers") or {}
            private = output.get("platform") == "hongguo"
            output["url"] = self.media.register(sid, url, headers,
                                                 "hls" if output.get("format") == "hls" else "binary", private)
            output["headers"] = {}
            # Local relay URLs allow native video and HLS to keep API keys and
            # required upstream request headers entirely on the server.
            for subtitle in output.get("subtitles") or []:
                if subtitle.get("url"):
                    subtitle["url"] = self.media.register(sid, subtitle["url"], headers, private=private)
        return output


class Handler(BaseHTTPRequestHandler):
    server_version = "ShortDrama/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Avoid logging query parameters, media URLs, configuration or keys.
        print(time.strftime("%H:%M:%S"), self.command, urlsplit(self.path).path, flush=True)

    @property
    def state(self):
        return self.server.state

    def session(self):
        match = re.search(r"(?:^|;\s*)shortdrama_session=([A-Za-z0-9_-]+)", self.headers.get("Cookie", ""))
        self.sid, self.provider_session = self.state.providers.get_session(match.group(1) if match else None)
        return self.sid, self.provider_session

    def respond(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if getattr(self, "sid", None):
            self.send_header("Set-Cookie", f"shortdrama_session={self.sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=7200")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def error_response(self, exc):
        if isinstance(exc, requests.HTTPError):
            upstream = exc.response.status_code if exc.response is not None else 502
            status = 502
            message = "平台暂时无法响应，请稍后重试"
            if upstream in {401, 403}:
                message = "平台要求授权；红果请检查后端 API Key，海外平台可前往官方页面"
            elif upstream == 429:
                message = "平台请求较多，请稍后重试"
            code = "upstream_http_" + str(upstream)
        elif isinstance(exc, requests.RequestException):
            status, message, code = 502, "连接平台超时或中断，请重试", "upstream_connection_error"
        elif isinstance(exc, ValueError):
            status, message, code = 400, str(exc), "invalid_request"
        else:
            status = 409 if getattr(exc, "code", None) == "configuration_required" else 502
            message = str(exc) if isinstance(exc, (AdapterError, RuntimeError)) else "服务暂时不可用，请重试"
            code = getattr(exc, "code", None) or "upstream_error"
        self.respond(status, {"ok": False, "error": {"code": code, "message": message}})

    def do_GET(self):
        try:
            self.session()
            path = urlsplit(self.path).path
            if path == "/api/platforms":
                self.respond(200, {"ok": True, "data": self.state.providers.platform_info(), "version": VERSION})
            elif path == "/api/config":
                self.respond(200, {"ok": True, "data": self.state.providers.config()})
            elif path == "/api/health":
                self.respond(200, {"ok": True, "version": VERSION, "platforms": 6})
            elif path.startswith("/api/"):
                self.dispatch_api(path, {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()})
            elif path.startswith("/media/"):
                self.media_response(path.split("/")[-1])
            else:
                self.static_response(path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.error_response(exc)

    def do_POST(self):
        try:
            self.session()
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.respond(403, {"ok": False, "error": {"message": "来源不受支持"}})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 65536:
                raise ValueError("请求大小无效")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("请求内容应为 JSON 对象")
            path = urlsplit(self.path).path
            if path == "/api/config":
                value = self.state.providers.update_config(data.get("hongguo_base_url", ""), data.get("api_key"))
                self.respond(200, {"ok": True, "data": value})
            elif path == "/api/config/test":
                if not self.state.providers.base_url:
                    raise AdapterError("请先填写并保存后端地址", code="configuration_required")
                # An authenticated catalog read tests the key as well as HTTP.
                with self.provider_session.lock:
                    client = self.state.providers.client(self.provider_session, "hongguo", "zh-CN")
                    raw = client._api("/browse", {"genre": "short_play", "limit": 1, "sort": "hot_score"})
                self.respond(200, {"ok": True, "data": {"connected": True, "sample_count": len(raw.get("items", []))}})
            else:
                self.dispatch_api(path, data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.error_response(exc)

    def dispatch_api(self, path, data):
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "api" or (parts[1] not in PLATFORM_IDS and parts[1] != "all"):
            self.respond(404, {"ok": False, "error": {"message": "接口不存在"}})
            return
        value = self.state.providers.call(self.provider_session, parts[1], parts[2], data)
        self.respond(200, {"ok": True, "data": self.state.decorate(value, self.sid)})

    def static_response(self, path):
        relative = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
        static_root = (ROOT / "static").resolve()
        target = (static_root / relative).resolve()
        if not target.is_relative_to(static_root) or not target.is_file():
            self.respond(404, b"Not found", "text/plain")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type in {"text/html", "text/css", "text/javascript", "application/javascript"}:
            content_type += "; charset=utf-8"
        self.respond(200, target.read_bytes(), content_type)

    def media_response(self, token):
        resource = self.state.media.get(token, getattr(self, "sid", None))
        headers = {**resource["headers"], "Accept-Encoding": "identity"}
        if "User-Agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        byte_range = self.headers.get("Range")
        if byte_range:
            if not re.fullmatch(r"bytes=\d*-\d*", byte_range):
                raise ValueError("不支持的 Range 请求")
            headers["Range"] = byte_range
        response = requests.get(resource["url"], headers=headers, timeout=60, stream=True)
        try:
            if response.status_code == 416:
                self.respond(416, b"", "application/octet-stream", {"Content-Range": response.headers.get("Content-Range", "bytes */0")})
                return
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            is_hls = resource["kind"] == "hls" or "mpegurl" in content_type.lower()
            if is_hls:
                response.raw.decode_content = True
                raw = response.raw.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("播放清单过大")
                rewritten = self.state.media.rewrite_manifest(raw.decode("utf-8-sig"), response.url, self.sid, resource)
                self.respond(200, rewritten.encode(), "application/vnd.apple.mpegurl")
                return
            self.send_response(response.status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "private, max-age=60")
            for key in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if response.headers.get(key):
                    self.send_header(key, response.headers[key])
            if not response.headers.get("Content-Length"):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            for chunk in response.iter_content(65536):
                if chunk:
                    self.wfile.write(chunk)
        finally:
            response.close()


def build_server(host="127.0.0.1", port=8790):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.state = AppState()
    return server


def main():
    parser = argparse.ArgumentParser(description="ShortDrama Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--no-hongguo-backend", action="store_true", help="关闭自动启动的红果官网后端")
    parser.add_argument("--hongguo-port", type=int, default=0, help="红果本地后端端口，默认自动选择")
    args = parser.parse_args()
    server = build_server(args.host, args.port)
    hongguo_server = None
    if not args.no_hongguo_backend and not server.state.providers.base_url:
        from hongguo_backend import build_backend
        try:
            hongguo_server = build_backend(port=args.hongguo_port)
        except Exception:
            server.server_close()
            raise
        backend_url = f"http://127.0.0.1:{hongguo_server.server_port}"
        server.state.providers.update_config(backend_url, hongguo_server.state.api_key)
        server.state.providers.managed_hongguo_url = backend_url
        threading.Thread(target=hongguo_server.serve_forever, daemon=True).start()
        print(f"Hongguo public backend — {backend_url}", flush=True)
    url = f"http://{args.host}:{server.server_port}"
    print(f"ShortDrama {VERSION} — {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.state.providers.close()
        if hongguo_server:
            hongguo_server.shutdown()
            hongguo_server.server_close()
            hongguo_server.state.close()


if __name__ == "__main__":
    main()
