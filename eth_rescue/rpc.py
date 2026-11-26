"""The node RPC transport. Public endpoints rate-limit and drop requests, so
each request backs off and retries, honoring Retry-After up to a cap. Only reads
go to the node (bundles go to the relay), so any request is safe to repeat."""

import random
import time
from collections.abc import Callable
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from web3 import HTTPProvider
from web3.types import RPCEndpoint, RPCResponse

RETRIES = 5
# Waits 0, 1, 2, 4, 8 seconds between transport attempts, plus jitter.
BACKOFF_FACTOR = 0.5
BACKOFF_JITTER = 0.5
# Waiting longer outlasts the blocks a bundle targets; the engine decides then.
MAX_WAIT = 30.0
RETRY_STATUSES = frozenset({429, 502, 503, 504})
# EIP-1474 "limit exceeded", and the HTTP status some providers put in the body.
RATE_LIMIT_CODES = frozenset({-32005, 429})


class _CappedRetry(Retry):
    def parse_retry_after(self, retry_after: str) -> float:
        return min(super().parse_retry_after(retry_after), MAX_WAIT)


def _session() -> requests.Session:
    retry = _CappedRetry(
        total=RETRIES,
        read=1,  # a read timeout already cost a full request timeout
        status_forcelist=RETRY_STATUSES,
        allowed_methods=None,  # JSON-RPC is all POST
        backoff_factor=BACKOFF_FACTOR,
        backoff_jitter=BACKOFF_JITTER,
        backoff_max=MAX_WAIT,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _rate_limited(response: RPCResponse) -> bool:
    error = response.get("error")
    return isinstance(error, dict) and error.get("code") in RATE_LIMIT_CODES


def _backoff(attempt: int) -> float:
    return min(MAX_WAIT, BACKOFF_FACTOR * 2 ** (attempt + 1)) + (
        random.random() * BACKOFF_JITTER
    )


class BackoffHTTPProvider(HTTPProvider):
    def __init__(
        self,
        endpoint_uri: str,
        sleep: Callable[[float], None] = time.sleep,
        **kwargs: Any,
    ):
        # The session's retries replace web3's own, which ignore Retry-After.
        super().__init__(
            endpoint_uri,
            session=_session(),
            exception_retry_configuration=None,
            **kwargs,
        )
        self._sleep = sleep

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        response = super().make_request(method, params)
        for attempt in range(RETRIES):
            if not _rate_limited(response):
                break
            self._sleep(_backoff(attempt))
            response = super().make_request(method, params)
        return response
