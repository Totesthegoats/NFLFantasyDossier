"""
images.py — Avatar/headshot/logo URL construction + local caching for the
HTML/PDF renderers.

Award cards (and the rivalry/draft-value tables) embed a small image where
there's a natural subject: a specific player gets a headshot, a manager's
overall performance gets their avatar, a trade gets both managers' avatars.
See render.py's `_card()` / `_award_image_html()` for how these get used —
this module only builds URLs and resolves them to embeddable data.

Images are fetched once and cached to disk as raw bytes (.cache/images/),
then re-served as base64 data URIs. Two reasons to embed rather than link:
  1. The PDF step (pdf.py) uses headless Chromium via Playwright, which is a
     real browser and WOULD fetch https:// images on its own — but that
     means every PDF render re-fetches every headshot fresh from Sleeper's
     CDN, with no cache across a batch run, and a hard dependency on the
     PDF-rendering machine having live network access at render time. Data
     URIs sidestep both: the HTML is fully self-contained, and our own
     on-disk cache (not Chromium's) is what gets reused across runs.
  2. The same self-contained HTML is also what batch.py emails directly —
     many mail clients block remote images until a user clicks "show
     images," but inline data URIs display immediately.

A bundled local SVG silhouette (no network) is the fallback for any missing
avatar or fetch failure (404, timeout) — never an external default image.
"""

from __future__ import annotations
import base64
import hashlib
import os

import requests

_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache", "images")
_FETCH_TIMEOUT = 4

_SILHOUETTE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<rect width="100" height="100" fill="#cdd5e3"/>'
    '<circle cx="50" cy="38" r="20" fill="#9aa7bd"/>'
    '<path d="M14 96c0-22 16-38 36-38s36 16 36 38" fill="#9aa7bd"/>'
    '</svg>'
)
SILHOUETTE_DATA_URI = (
    "data:image/svg+xml;base64," + base64.b64encode(_SILHOUETTE_SVG.encode("utf-8")).decode("ascii")
)


def player_headshot_url(player_id, thumb: bool = True) -> str:
    if not player_id:
        return ""
    variant = "thumb/" if thumb else ""
    return f"https://sleepercdn.com/content/nfl/players/{variant}{player_id}.jpg"


def manager_avatar_url(team) -> str:
    """`team` is a data.Team. Custom-uploaded avatars come back from Sleeper
    as a full URL in metadata.avatar (Team.avatar_full_url); everyone else
    just has a Sleeper avatar ID (Team.avatar) that needs the CDN path
    built. Either field can in principle already be a full URL, so check
    before building one."""
    if not team:
        return ""
    full = getattr(team, "avatar_full_url", None)
    if full:
        return full
    avatar = getattr(team, "avatar", None)
    if not avatar:
        return ""
    if avatar.startswith("http"):
        return avatar
    return f"https://sleepercdn.com/avatars/thumbs/{avatar}"


def team_logo_url(team_abbr) -> str:
    if not team_abbr:
        return ""
    return f"https://sleepercdn.com/images/team_logos/nfl/{team_abbr.lower()}.png"


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _sniff_ext(data: bytes) -> str:
    """Sleeper's CDN sometimes serves a Content-Type header that doesn't
    match the actual bytes (e.g. a PNG body behind a .jpg URL with a
    `Content-Type: image/jpeg` header) — sniff the real magic bytes rather
    than trusting either the header or the URL extension."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:5] == b"<?xml" or b"<svg" in data[:200]:
        return "svg"
    return "jpg"


def _mime_for_ext(ext: str) -> str:
    if ext == "svg":
        return "image/svg+xml"
    if ext == "jpg":
        return "image/jpeg"
    return f"image/{ext}"


def to_data_uri(url: str) -> str:
    """Fetch `url`, cache the raw bytes to disk keyed by a hash of the URL,
    and return a base64 data URI — the bundled silhouette on any failure
    (no URL, 404, timeout). Successes are cached forever, same as this
    codebase's other disk caches (a past week's headshot never changes).
    Failures are cached too via an empty `.fail` marker, so a permanently
    404-ing player/avatar ID isn't re-fetched on every future run."""
    if not url:
        return SILHOUETTE_DATA_URI

    os.makedirs(_CACHE_DIR, exist_ok=True)
    key = _cache_key(url)
    fail_path = os.path.join(_CACHE_DIR, f"{key}.fail")
    if os.path.exists(fail_path):
        return SILHOUETTE_DATA_URI

    for ext in ("jpg", "png", "gif", "svg"):
        path = os.path.join(_CACHE_DIR, f"{key}.{ext}")
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            return f"data:{_mime_for_ext(ext)};base64,{base64.b64encode(data).decode('ascii')}"

    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.content
        ext = _sniff_ext(data)
        try:
            with open(os.path.join(_CACHE_DIR, f"{key}.{ext}"), "wb") as f:
                f.write(data)
        except OSError:
            pass
        return f"data:{_mime_for_ext(ext)};base64,{base64.b64encode(data).decode('ascii')}"
    except requests.RequestException:
        try:
            with open(fail_path, "w") as f:
                pass
        except OSError:
            pass
        return SILHOUETTE_DATA_URI
