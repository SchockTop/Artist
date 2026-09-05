"""Polite HTTP client (standard library only).

Kleinanzeigen answers plain ``urllib`` requests as long as you look like a
browser and do not hammer it.  The client therefore throttles every request,
retries with exponential backoff on the 403/429 the site uses for rate
limiting, and can cache responses on disk so repeated runs (and development)
cost nothing.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import pathlib
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

log = logging.getLogger(__name__)

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

RETRY_STATUS = {403, 408, 429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{status or 'ERR'} for {url}: {message}")
        self.url = url
        self.status = status


class HttpClient:
    def __init__(
        self,
        delay: float = 2.0,
        jitter: float = 0.75,
        retries: int = 4,
        timeout: float = 30.0,
        cache_dir: str | pathlib.Path | None = None,
        cache_ttl: float = 900.0,
        user_agent: str | None = None,
    ):
        self.delay = delay
        self.jitter = jitter
        self.retries = retries
        self.timeout = timeout
        self.cache_dir = pathlib.Path(cache_dir).expanduser() if cache_dir else None
        self.cache_ttl = cache_ttl
        self.user_agent = user_agent or random.choice(DEFAULT_USER_AGENTS)
        self.request_count = 0
        self._last_request = 0.0
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cache
    def _cache_path(self, url: str) -> pathlib.Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.html.gz"

    def _cache_read(self, url: str) -> str | None:
        path = self._cache_path(url)
        if not path or not path.exists():
            return None
        if self.cache_ttl and (time.time() - path.stat().st_mtime) > self.cache_ttl:
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def _cache_write(self, url: str, body: str) -> None:
        path = self._cache_path(url)
        if not path:
            return
        try:
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write(body)
        except OSError as exc:  # pragma: no cover - cache is best effort
            log.debug("cache write failed: %s", exc)

    # ----------------------------------------------------------------- fetch
    def _throttle(self) -> None:
        wait = self.delay + random.uniform(0, self.jitter) - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    @staticmethod
    def _decode(raw: bytes, encoding: str | None) -> str:
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        return raw.decode("utf-8", "replace")

    def get(self, url: str, accept: str = "text/html", referer: str | None = None, use_cache: bool = True) -> str:
        if use_cache:
            cached = self._cache_read(url)
            if cached is not None:
                log.debug("cache hit %s", url)
                return cached

        headers = {
            "User-Agent": self.user_agent,
            "Accept": f"{accept},*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if referer:
            headers["Referer"] = referer

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            self.request_count += 1
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    body = self._decode(resp.read(), resp.headers.get("Content-Encoding"))
                if use_cache:
                    self._cache_write(url, body)
                return body
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in RETRY_STATUS or attempt == self.retries:
                    raise HttpError(url, exc.code, exc.reason or "http error") from exc
                backoff = self.delay * (2 ** attempt) + random.uniform(0, 1.5)
                log.warning("HTTP %s for %s - retrying in %.1fs", exc.code, url, backoff)
                time.sleep(backoff)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.retries:
                    raise HttpError(url, None, str(exc)) from exc
                backoff = self.delay * (2 ** attempt) + random.uniform(0, 1.5)
                log.warning("network error for %s (%s) - retrying in %.1fs", url, exc, backoff)
                time.sleep(backoff)
        raise HttpError(url, None, str(last_error))

    def get_json(self, url: str, use_cache: bool = True):
        return json.loads(self.get(url, accept="application/json", use_cache=use_cache))

    def resolve_redirect(self, url: str) -> str:
        """Follow redirects and return the final URL (used for maps short links)."""
        self._throttle()
        self.request_count += 1
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return resp.geturl()
        except urllib.error.HTTPError as exc:
            return exc.geturl() or url
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HttpError(url, None, str(exc)) from exc


def quote_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")
