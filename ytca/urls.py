"""Channel / video URL validation and normalisation (pure string handling, no network)."""
import re
import unicodedata
import urllib.parse

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
# YouTube handles: 3-30 chars, letters (any script), digits, underscore, hyphen, period.
HANDLE_RE = re.compile(r"^[\w.\-]{3,30}$")
CHANNEL_TABS = {"", "videos", "shorts", "streams", "featured", "playlists", "about", "community", "podcasts"}
VIDEO_PATH_PREFIXES = ("shorts", "live", "embed")


class UrlError(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _split(value):
    v = (value or "").strip()
    if not v:
        raise UrlError("EMPTY", "no URL given")
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    u = urllib.parse.urlsplit(v)
    return u, (u.hostname or "").lower()


def normalize_channel(value):
    """Return {kind, key, handle|channel_id, canonical_url}. Accepts @handle, youtube.com/@handle[/tab],
    youtube.com/channel/UC… (optionally percent-encoded). Anything else raises UrlError."""
    raw = (value or "").strip()
    if raw.startswith("@"):
        raw = "https://www.youtube.com/" + raw
    u, host = _split(raw)
    if host not in YT_HOSTS:
        raise UrlError("NOT_YOUTUBE", f"host {host!r} is not youtube.com")
    parts = [urllib.parse.unquote(p) for p in u.path.split("/") if p]
    if not parts:
        raise UrlError("NOT_A_CHANNEL", "no channel path")
    if parts[0].startswith("@"):
        handle = unicodedata.normalize("NFC", parts[0][1:])
        tab = parts[1].lower() if len(parts) > 1 else ""
        if len(parts) > 2 or tab not in CHANNEL_TABS:
            raise UrlError("NOT_A_CHANNEL", f"unexpected path after handle: {'/'.join(parts[1:])}")
        if not HANDLE_RE.fullmatch(handle):
            raise UrlError("BAD_HANDLE", f"invalid handle {handle!r}")
        key = handle.casefold()
        return {"kind": "handle", "key": key, "handle": "@" + handle, "channel_id": None,
                "canonical_url": "https://www.youtube.com/@" + urllib.parse.quote(handle)}
    if parts[0] == "channel" and len(parts) >= 2:
        cid = parts[1]
        if not CHANNEL_ID_RE.fullmatch(cid):
            raise UrlError("BAD_CHANNEL_ID", f"invalid channel id {cid!r}")
        if len(parts) > 3 or (len(parts) == 3 and parts[2].lower() not in CHANNEL_TABS):
            raise UrlError("NOT_A_CHANNEL", "unexpected path after channel id")
        return {"kind": "channel_id", "key": cid, "handle": None, "channel_id": cid,
                "canonical_url": "https://www.youtube.com/channel/" + cid}
    if parts[0] in ("c", "user"):
        raise UrlError("LEGACY_URL", "legacy /c/ or /user/ URL: please give the @handle or /channel/UC... URL")
    raise UrlError("NOT_A_CHANNEL", "this is not a channel URL (single videos and playlists are not channels)")


def normalize_video(value):
    """Return the 11-char video id from a watch / youtu.be / shorts / live / embed URL or a bare id."""
    v = (value or "").strip()
    if VIDEO_ID_RE.fullmatch(v):
        return v
    u, host = _split(v)
    if host in SHORT_HOSTS:
        vid = next((p for p in u.path.split("/") if p), "")
    elif host in YT_HOSTS:
        parts = [p for p in u.path.split("/") if p]
        if parts[:1] == ["watch"]:
            vid = (urllib.parse.parse_qs(u.query).get("v") or [""])[0]
        elif len(parts) >= 2 and parts[0] in VIDEO_PATH_PREFIXES:
            vid = parts[1]
        else:
            raise UrlError("NOT_A_VIDEO", "not a single-video URL")
    else:
        raise UrlError("NOT_YOUTUBE", f"host {host!r} is not YouTube")
    if not VIDEO_ID_RE.fullmatch(vid):
        raise UrlError("BAD_VIDEO_ID", f"invalid video id {vid!r}")
    return vid


def video_url(video_id):
    return "https://www.youtube.com/watch?v=" + video_id
