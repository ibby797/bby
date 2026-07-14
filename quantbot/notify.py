"""Optional webhook notifications (Discord/Slack-compatible JSON payload).

Failures are logged and swallowed — notifications must never break trading.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url

    def send(self, message: str) -> None:
        log.info("NOTIFY: %s", message)
        if not self.webhook_url:
            return
        try:
            requests.post(
                self.webhook_url,
                json={"content": message, "text": message},
                timeout=10,
            )
        except requests.RequestException as exc:
            log.warning("webhook notification failed: %s", exc)
