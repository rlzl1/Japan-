"""HTTP client with retries, per-host politeness delay and an on-disk cache.

Every downloader in the package goes through `Http.get`, so a re-run of the screen only fetches
what changed (filings are immutable once published; lists/prices use a short TTL).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; jpscreen/1.0; +https://github.com/rlzl1/Japan-)"


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.url = url
        self.status = status


class Http:
    def __init__(self, cache_dir: str | Path = ".cache/http", min_interval: float = 0.35,
                 timeout: float = 60.0, retries: int = 4, offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.offline = offline
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
        ca = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca and Path(ca).exists():
            self.session.verify = ca
        self._last_hit: dict[str, float] = {}

    def _cache_path(self, url: str, params: Optional[dict]) -> Path:
        # Never let secrets (API keys) become part of what we print, but they must still key the cache.
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        h = hashlib.sha1(key.encode()).hexdigest()
        return self.cache_dir / h[:2] / h

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str, params: Optional[dict] = None, ttl: Optional[float] = None,
            headers: Optional[dict] = None, allow_404: bool = False, store: bool = True) -> Optional[bytes]:
        """GET with cache. ttl=None caches forever, ttl=0 never reads the cache.

        Returns None for 404 when allow_404 is set (TDnet deletes documents after ~31 days).
        """
        path = self._cache_path(url, params)
        if ttl != 0 and path.exists():
            age = time.time() - path.stat().st_mtime
            if ttl is None or age < ttl:
                data = path.read_bytes()
                if data == b"__404__":
                    return None
                return data
        if self.offline:
            if path.exists():
                data = path.read_bytes()
                return None if data == b"__404__" else data
            raise HttpError(url, 0, "offline mode and not cached")

        err: Optional[Exception] = None
        for attempt in range(self.retries):
            self._throttle(url)
            try:
                r = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
            except requests.RequestException as e:  # network blip: back off and retry
                err = e
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code == 200:
                if store:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(r.content)
                return r.content
            if r.status_code == 404 and allow_404:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"__404__")
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                retry_after = r.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt + 1))
                err = HttpError(url, r.status_code, r.text)
                continue
            raise HttpError(url, r.status_code, r.text)
        raise err if err else HttpError(url, 0, "unknown failure")

    def invalidate(self, url: str, params: Optional[dict] = None) -> None:
        """Forget a cached response (e.g. an API error body that came back with HTTP 200)."""
        self._cache_path(url, params).unlink(missing_ok=True)

    def get_json(self, url: str, params: Optional[dict] = None, ttl: Optional[float] = None,
                 headers: Optional[dict] = None):
        data = self.get(url, params=params, ttl=ttl, headers=headers)
        return json.loads(data.decode("utf-8")) if data is not None else None

    def get_text(self, url: str, params: Optional[dict] = None, ttl: Optional[float] = None,
                 encoding: str = "utf-8", allow_404: bool = False) -> Optional[str]:
        data = self.get(url, params=params, ttl=ttl, allow_404=allow_404)
        return data.decode(encoding, errors="replace") if data is not None else None
