"""Bounded navigation evidence; never collect cookies, headers or form data."""
import re
from collections import deque
from urllib.parse import urlsplit, urlunsplit


class SiteRejectedError(Exception):
    def __init__(self, support_id=None):
        self.support_id = support_id
        super().__init__("Site rejected the request; administrator review required")


def public_url(url):
    parts = urlsplit(str(url))
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def rejection_info(text):
    if "the requested url was rejected" not in str(text).lower():
        return None
    match = re.search(r"support id(?: is)?\s*:\s*<?([0-9]+)", text, re.I)
    return {"support_id": match.group(1) if match else None}


def check_rejection(page):
    try:
        text = page.inner_text("body", timeout=2000)
    except Exception:
        # A missing body during navigation is not evidence of rejection.
        return
    info = rejection_info(text)
    if info is not None:
        raise SiteRejectedError(info["support_id"])


class NavigationEvidence:
    def __init__(self, page):
        self.events = deque(maxlen=80)
        page.on("response", self.response)
        page.on("requestfailed", self.failed)
        # Error messages can contain personal data; collect only event counts.
        page.on("pageerror", lambda error: self.events.append({"event": "pageerror"}))

    def response(self, response):
        request = response.request
        if request.is_navigation_request():
            self.events.append({"event": "response", "url": public_url(response.url),
                                "method": request.method, "status": response.status,
                                "redirect": bool(request.redirected_from)})

    def failed(self, request):
        if request.is_navigation_request():
            self.events.append({"event": "requestfailed", "url": public_url(request.url),
                                "method": request.method})

    def snapshot(self, page):
        return {"final_url": public_url(page.url), "events": list(self.events)}
