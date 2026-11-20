"""Single HTTP entry point: timeouts, retries and a polite User-Agent."""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; FeedAggregator/1.0; "
    "+https://github.com/) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def get(
    url: str,
    *,
    timeout: float = 15,
    retries: int = 2,
    user_agent: str = DEFAULT_USER_AGENT,
    accept: Optional[str] = None,
) -> Optional[requests.Response]:
    """GET a URL. Returns the response, or None on permanent failure. Never raises."""
    headers = {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": accept or "*/*",
        "Accept-Language": "en,zh;q=0.8,*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    retries = max(0, int(retries))
    last_error: object = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Request failed (attempt %d/%d) %s: %s", attempt + 1, retries + 1, url, exc)
        else:
            status = response.status_code
            if status in RETRYABLE_STATUS:
                last_error = f"HTTP {status}"
                logger.warning("HTTP %s (attempt %d/%d) %s", status, attempt + 1, retries + 1, url)
            elif status >= 400:
                logger.warning("HTTP %s, giving up on %s", status, url)
                return None
            else:
                return response

        if attempt < retries:
            time.sleep(min(2**attempt, 5))

    logger.warning("Request permanently failed: %s (%s)", url, last_error)
    return None
