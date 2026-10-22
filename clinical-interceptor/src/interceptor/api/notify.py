"""Notifications for the clinical safety team (PRD journey C and D).

A message says that something happened and where to look. It never carries note text, claim text or a patient name: only
IDs, the event name and the rule codes. Sending is best effort. A failed or slow webhook must never change a verdict or
release content, so errors are swallowed.
"""

import json
import logging
import urllib.request

log = logging.getLogger("interceptor.notify")

NOTE_QUARANTINED = "NOTE_QUARANTINED"
VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
ALLOWED_FIELDS = ("event", "note_id", "report_id", "patient_pseudo_id", "rules", "critical_count")


class NullNotifier:
    def notify(self, event):
        return None


class WebhookNotifier:
    def __init__(self, url, timeout=2.0):
        if not url.startswith("https://"):
            raise ValueError("the notification webhook must be an https URL")
        self.url, self.timeout = url, timeout

    def notify(self, event):
        body = json.dumps({k: event[k] for k in ALLOWED_FIELDS if k in event}).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})  # noqa: S310 (https only)
        try:
            urllib.request.urlopen(request, timeout=self.timeout).close()  # noqa: S310 (https is checked in __init__)
        except Exception as exc:  # best effort: see the module note
            log.warning("notification not sent: %s", type(exc).__name__)


def send(state, event):
    """Sends through the app's notifier. Never raises."""
    notifier = getattr(state, "notifier", None)
    if notifier is None:
        return
    try:
        notifier.notify(event)
    except Exception as exc:
        log.warning("notifier failed: %s", type(exc).__name__)
