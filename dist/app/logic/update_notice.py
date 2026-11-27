"""Startup update notice: GitHub's latest release against APP_VERSION.

Tags may be written any way that leads with dotted numbers ("v1.4", "v-2.2");
only those numbers are compared. Checked on each startup so a newer release
briefly appears in the UI and fades out. Offline, rate-limited or no release yet: None.
"""

import json
import re
import urllib.request

from ..config import APP_NAME, APP_VERSION, RELEASES_API


def _numbers(text):
    match = re.search(r"\d+(?:\.\d+)*", text or "")
    parts = [int(n) for n in match.group().split(".")] if match else []
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _fetch_latest():
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": APP_NAME,
    })
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    except Exception:
        return None


def pending_notice():
    """Return the latest newer release to announce, or None."""
    release = _fetch_latest()
    if not isinstance(release, dict):
        return None
    tag = str(release.get("tag_name") or "")
    url = str(release.get("html_url") or "")
    latest = _numbers(tag)
    if not latest or latest <= _numbers(APP_VERSION) or not url.startswith("https://github.com/"):
        return None
    return {"version": "v" + re.search(r"\d+(?:\.\d+)*", tag).group(), "url": url}
