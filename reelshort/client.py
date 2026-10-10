"""Transport for ReelShort's public website, using its current browser protocol.

Browser configuration is read from the site's own published JavaScript. Nothing
here changes account entitlements or decrypts protected video content.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import string
import time
import zlib
from urllib.parse import urljoin

import requests


class ReelShortError(RuntimeError):
    def __init__(self, message, *, code=None, http_status=None, path=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.path = path


class ReelShortClient:
    BASE_URL = "https://www.reelshort.com"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )

    def __init__(self, *, timeout=30, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self.device_id = "".join(secrets.choice(string.ascii_letters + string.digits)
                                 for _ in range(12)) + str(int(time.time() * 1000))
        self.uid = "0"
        self.account_session = ""
        self._browser_config = None
        self.request_log = []

    def get_html(self, path="/en", language="en"):
        url = urljoin(self.BASE_URL, path)
        if url.split("/", 3)[:3] != self.BASE_URL.split("/", 3)[:3]:
            raise ValueError("HTML requests must use the official ReelShort origin")
        response = self.session.get(url, timeout=self.timeout,
                                    headers={"Accept-Language": language})
        response.raise_for_status()
        self.request_log.append({"method": "GET", "path": path,
                                 "http_status": response.status_code})
        return response.text

    @staticmethod
    def next_data(html):
        match = re.search(r'<script[^>]+id=[\"\']__NEXT_DATA__[\"\'][^>]*>(.*?)</script>',
                          html, re.S)
        if not match:
            raise ReelShortError("Website response contains no Next.js page data")
        return json.loads(match.group(1))

    def _load_browser_config(self):
        if self._browser_config is not None:
            return self._browser_config
        html = self.get_html("/en")
        scripts = re.findall(r'<script[^>]*src=[\"\']([^\"\']+)', html)
        app_script = next((src for src in scripts if "/pages/_app-" in src), None)
        if app_script is None:
            raise ReelShortError("Website browser bundle changed; transport requires review")
        response = self.session.get(urljoin(self.BASE_URL, app_script), timeout=self.timeout)
        response.raise_for_status()
        js = response.text
        signing = re.search(r'var E=m\.locales,S="([^"\\]+)"', js)
        response_config = re.search(
            r'55598:function.*?\],o="([^"\\]+)",i="([^"\\]+)"', js, re.S)
        version = re.search(r'apiVersion:.*?\|\|"([^"\\]+)"', js)
        client_version = re.search(r'clientVer:"([^"\\]+)"', js)
        if not all((signing, response_config, version, client_version)):
            raise ReelShortError("Website browser configuration changed; transport requires review")
        self._browser_config = {
            "signing_key": signing.group(1).encode(),
            "response_key": response_config.group(1).encode(),
            "response_iv": response_config.group(2).encode(),
            "api_version": version.group(1),
            "client_version": client_version.group(1),
            "bundle_url": urljoin(self.BASE_URL, app_script),
        }
        return self._browser_config

    @staticmethod
    def _js_value(value):
        if isinstance(value, (dict, list)) or value is None:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _headers(self, data, language):
        config = self._load_browser_config()
        headers = {
            "apiVersion": config["api_version"], "channelId": "WEB41001",
            "session": self.account_session, "clientVer": config["client_version"],
            "devId": self.device_id, "lang": language, "ts": str(int(time.time())),
            "uid": self.uid,
        }
        values = {**(data or {}), **headers}
        canonical = "&".join(
            f"{key}={self._js_value(value)}" for key, value in sorted(values.items())
            if value is not None and self._js_value(value) not in ("", "null")
        )
        headers["sign"] = hmac.new(config["signing_key"], canonical.encode(),
                                    hashlib.sha256).hexdigest()
        headers.update({"Origin": self.BASE_URL, "Referer": f"{self.BASE_URL}/",
                        "Accept": "application/json, text/plain, */*"})
        return headers

    def _decode_response(self, response):
        try:
            result = response.json()
            if not isinstance(result, str):
                return result
            text = result
        except ValueError:
            text = response.text.strip()
        config = self._load_browser_config()
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import unpad
        except ImportError as exc:
            raise ReelShortError("Encrypted API responses require pycryptodome") from exc
        try:
            plain = unpad(AES.new(config["response_key"], AES.MODE_CBC,
                                  config["response_iv"]).decrypt(base64.b64decode(text)), 16)
            # pako.inflate() in the website accepts either zlib or gzip wrappers.
            return json.loads(zlib.decompress(base64.b64decode(plain), wbits=47))
        except Exception as exc:
            raise ReelShortError("Cannot decode website API response; protocol may have changed") from exc

    def request(self, path, method="POST", data=None, language="en"):
        if not path.startswith("/api/"):
            raise ValueError("Only official website API paths are supported")
        headers = self._headers(data, language)
        kwargs = {"headers": headers, "timeout": self.timeout}
        if method.upper() == "GET":
            kwargs["params"] = data
        else:
            kwargs["json"] = data or {}
        response = self.session.request(method, self.BASE_URL + path, **kwargs)
        entry = {"method": method.upper(), "path": path,
                 "http_status": response.status_code}
        self.request_log.append(entry)
        if not response.ok:
            raise ReelShortError("Website API HTTP error", http_status=response.status_code, path=path)
        result = self._decode_response(response)
        if not isinstance(result, dict):
            raise ReelShortError("Website API returned an unexpected response type", path=path)
        entry["code"] = result.get("code")
        if result.get("code") not in (None, 0):
            raise ReelShortError(str(result.get("message") or result.get("msg") or "Website API error"),
                                 code=result.get("code"), path=path)
        return result

    def ensure_guest(self, language="en"):
        """Initialize the same sid=0 visitor session as the public website.

        Session credentials stay in memory and confer no paid entitlements.
        """
        if self.uid != "0" and self.account_session:
            return
        result = self.request("/api/video/user/userLogin", data={
            "uname": "", "sid": 0, "openid": self.device_id, "pic": "",
            "media_type": "", "pay_mode": "", "dev_model": "h5",
        }, language=language)
        data = result.get("data") or {}
        user = data.get("user_info") or {}
        if not user.get("uid") or not data.get("session"):
            raise ReelShortError("Website did not return a visitor session")
        self.uid = str(user["uid"])
        self.account_session = str(data["session"])

    def close(self):
        self.session.close()
