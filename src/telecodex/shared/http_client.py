from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
)


@dataclass
class HttpTimeoutConfig:
    connect_sec: float
    read_sec: float
    write_sec: float
    pool_sec: float

    def build(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_sec,
            read=self.read_sec,
            write=self.write_sec,
            pool=self.pool_sec,
        )


class ResilientHttpClient:
    def __init__(
        self,
        timeout: HttpTimeoutConfig,
        max_retries: int = 2,
        backoff_sec: float = 0.5,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec
        self._client = client or httpx.Client(timeout=timeout.build())

    def request(self, method: str, url: str, *, retryable: bool = False, **kwargs) -> httpx.Response:
        attempts = self.max_retries + 1 if retryable else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(method, url, **kwargs)
            except RETRYABLE_EXCEPTIONS:
                if retryable and attempt + 1 < attempts:
                    self._sleep(attempt)
                    continue
                raise
            if retryable and response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < attempts:
                response.close()
                self._sleep(attempt)
                continue
            return response
        raise RuntimeError("unreachable retry loop")

    def get(self, url: str, *, retryable: bool = True, **kwargs) -> httpx.Response:
        return self.request("GET", url, retryable=retryable, **kwargs)

    def post(self, url: str, *, retryable: bool = False, **kwargs) -> httpx.Response:
        return self.request("POST", url, retryable=retryable, **kwargs)

    def close(self) -> None:
        self._client.close()

    def _sleep(self, attempt: int) -> None:
        time.sleep(self.backoff_sec * (attempt + 1))
