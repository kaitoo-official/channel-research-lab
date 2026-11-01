"""Local storage for one channel study: channel.json, videos.json, materials/, analyses/, journal.json.

Write boundary: everything stays under <root>/channels/<key>/. Writes are atomic (tmp + replace).
Every video field carries its provenance. Only "human" is ever written: API data is never stored (retention
model A, docs/api_retention_decision.md). expire_api_fields / ytca.api.purge_api_data only clean up old data.
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from .urls import normalize_channel, normalize_video, video_url

VIDEO_STATUSES = ("UNANALYZED", "PARTIAL", "ANALYZED", "ON_HOLD", "BLOCKED")
PROVENANCE = ("human",)            # "api" values are never stored (retention model A)
LOCK_STALE_SECONDS = 2 * 3600
SAFE_KEY_RE = re.compile(r"^[\w.\-]{1,40}$")


class StoreError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def now_iso(clock=None):
    t = clock() if clock else time.time()
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


class Store:
    def __init__(self, root, clock=None):
        self.root = Path(root)
        self.clock = clock

    def now(self):
        return now_iso(self.clock)

    # ------------------------------------------------------------ channel
    def channel_dir(self, key):
        if not SAFE_KEY_RE.fullmatch(key or ""):
            raise StoreError("BAD_KEY", key)
        d = (self.root / "channels" / key).resolve()
        if (self.root / "channels").resolve() not in d.parents:
            raise StoreError("BAD_KEY", key)
        return d

    def add_channel(self, url, title=None):
        info = normalize_channel(url)
        d = self.channel_dir(info["key"])
        existing = read_json(d / "channel.json")
        if existing:
            return existing, False
        ch = {"schema_version": SCHEMA_VERSION, **info, "input_url": url.strip(), "registered_at": self.now(),
              "title": {"value": title, "source": "human"} if title else None,
              "video_list": {"complete": False, "confirmed_by": None, "confirmed_at": None, "note": None}}
        write_json(d / "channel.json", ch)
        write_json(d / "videos.json", {"schema_version": SCHEMA_VERSION, "videos": {}})
        return ch, True

    def get_channel(self, key):
        ch = read_json(self.channel_dir(key) / "channel.json")
        if not ch:
            raise StoreError("UNKNOWN_CHANNEL", key)
        return ch

    def confirm_video_list(self, key, confirmed_by, note):
        """Only a person can declare the video list complete (needed before a coverage rate is shown)."""
        if not (confirmed_by or "").strip() or not (note or "").strip():
            raise StoreError("CONFIRMATION_NEEDS_PERSON_AND_NOTE")
        ch = self.get_channel(key)
        ch["video_list"] = {"complete": True, "confirmed_by": confirmed_by.strip(), "confirmed_at": self.now(),
                            "note": note.strip()}
        write_json(self.channel_dir(key) / "channel.json", ch)
        return ch

    # ------------------------------------------------------------ videos
    def videos(self, key):
        return read_json(self.channel_dir(key) / "videos.json", {"videos": {}})

    def save_videos(self, key, data):
        write_json(self.channel_dir(key) / "videos.json", data)

    def add_videos(self, key, entries, note=None):
        """entries: list of {url, title?, published_at?} typed or pasted by a person. Returns (added, known)."""
        self.get_channel(key)
        data = self.videos(key)
        added, known = [], []
        for e in entries:
            vid = normalize_video(e["url"])
            title = (e.get("title") or "").strip() or None
            pub = (e.get("published_at") or "").strip() or None
            if pub and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub):
                raise StoreError("BAD_DATE", f"{vid}: published_at must be YYYY-MM-DD")
            v = data["videos"].get(vid)
            if v is None:
                v = {"video_id": vid, "url": video_url(vid), "status": "UNANALYZED", "hold_reason": None,
                     "fields": {}, "sources": [], "materials": [], "analysis": False,
                     "added_at": self.now(), "last_checked_at": self.now()}
                data["videos"][vid] = v
                added.append(vid)
            else:
                known.append(vid)
            for name, val in (("title", title), ("published_at", pub)):
                cur = v["fields"].get(name)
                if val and (cur is None or cur["source"] == "human"):
                    v["fields"][name] = {"value": val, "source": "human", "at": self.now()}
            v["sources"].append({"type": "human", "at": self.now(), "note": note})
            v["last_checked_at"] = self.now()
        self.save_videos(key, data)
        return added, known

    def add_article_item(self, key, article_url, title=None, published_at=None, note=None):
        """A creator article that links no specific video (kind=article). Kept in videos.json next to the videos
        so materials / analyses work the same way, but never counted as a video. Returns (item_id, new)."""
        self.get_channel(key)
        url = (article_url or "").strip()
        if not re.match(r"^https?://", url):
            raise StoreError("SOURCE_URL_REQUIRED", "article item needs the article URL")
        slug = re.sub(r"[^\w.\-]", "", url.rstrip("/").split("?")[0].rsplit("/", 1)[-1])[:30]
        if not slug:
            raise StoreError("BAD_ARTICLE_URL", url)
        item_id = f"art-{slug}"
        if published_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_at):
            raise StoreError("BAD_DATE", "published_at must be YYYY-MM-DD")
        data = self.videos(key)
        v = data["videos"].get(item_id)
        if v and v.get("url") != url:
            raise StoreError("ARTICLE_ID_CLASH", f"{item_id} is already used by {v.get('url')}")
        new = v is None
        if new:
            v = {"video_id": item_id, "kind": "article", "url": url, "status": "UNANALYZED", "hold_reason": None,
                 "fields": {}, "sources": [], "materials": [], "analysis": False,
                 "added_at": self.now(), "last_checked_at": self.now()}
            data["videos"][item_id] = v
        for name, val in (("title", (title or "").strip() or None), ("published_at", published_at)):
            if val and v["fields"].get(name) is None:
                v["fields"][name] = {"value": val, "source": "human", "at": self.now()}
        v["sources"].append({"type": "human", "at": self.now(), "note": note})
        self.save_videos(key, data)
        return item_id, new

    def link_same_content(self, key, item_id, video_id, basis, note=None):
        """Record that an article item covers the same talk as a registered video (e.g. the article is the text
        version of that video). Synthesis then counts them as ONE origin. basis says why (ARTICLE_LINK when the
        article links the video, TITLE_MATCH when only the titles match — an inference)."""
        if basis not in ("ARTICLE_LINK", "TITLE_MATCH"):
            raise StoreError("BAD_BASIS", basis)
        data = self.videos(key)
        item, video = data["videos"].get(item_id), data["videos"].get(video_id)
        if not item or item.get("kind") != "article":
            raise StoreError("NOT_AN_ARTICLE_ITEM", item_id)
        if not video or video.get("kind") == "article":
            raise StoreError("UNKNOWN_VIDEO", video_id)
        item["same_content_as"] = {"video_id": video_id, "basis": basis, "note": note, "at": self.now()}
        self.save_videos(key, data)
        return item["same_content_as"]

    def set_status(self, key, video_id, status, reason=None):
        if status not in VIDEO_STATUSES:
            raise StoreError("BAD_STATUS", status)
        if status in ("ON_HOLD", "BLOCKED") and not (reason or "").strip():
            raise StoreError("REASON_REQUIRED", status)
        data = self.videos(key)
        v = data["videos"].get(video_id)
        if not v:
            raise StoreError("UNKNOWN_VIDEO", video_id)
        v["status"], v["hold_reason"] = status, (reason or None)
        v["last_checked_at"] = self.now()
        self.save_videos(key, data)

    def expire_api_fields(self, key, max_age_days=30):
        """Remove any field whose source is 'api' and that is older than max_age_days (Developer Policies
        storage limit). Human-entered fields are never touched. Returns the number of removed fields."""
        data = self.videos(key)
        limit = datetime.fromtimestamp(self.clock() if self.clock else time.time(), timezone.utc) \
            - timedelta(days=max_age_days)
        removed = 0
        for v in data["videos"].values():
            for name in list(v["fields"]):
                f = v["fields"][name]
                if f["source"] == "api":
                    at = datetime.strptime(f["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    if at < limit:
                        del v["fields"][name]
                        removed += 1
        self.save_videos(key, data)
        return removed

    # ------------------------------------------------------------ lock / journal
    def lock(self, key):
        return _Lock(self.channel_dir(key) / "lock.json", self.clock)

    def journal(self, key):
        return read_json(self.channel_dir(key) / "journal.json", {"done": {}})

    def save_journal(self, key, j):
        write_json(self.channel_dir(key) / "journal.json", j)


class _Lock:
    def __init__(self, path, clock):
        self.path, self.clock = path, clock

    def __enter__(self):
        t = self.clock() if self.clock else time.time()
        if self.path.exists():
            info = read_json(self.path, {}) or {}
            if t - info.get("t", 0) < LOCK_STALE_SECONDS:
                raise StoreError("LOCKED", f"another run holds {self.path.name}")
        write_json(self.path, {"t": t, "pid": os.getpid()})
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
