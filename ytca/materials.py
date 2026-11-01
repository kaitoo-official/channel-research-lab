"""Analysis material intake. Five material types; each carries a 'basis' that later decides how far a
claim built on it can be trusted. An AI summary is never treated as a first-hand check of the video.

A creator_article also records where it came from (title, publish date) and what it links to: the video it
cites (kept apart from the article itself, since reading the article is not watching the video) and the
channel it names. The channel link is CHANNEL_ID_UNVERIFIED unless both sides carry the same channel ID —
similar-looking handles (e.g. ず / ズ) are never taken as proof."""
import re

from .store import StoreError, read_json, sha256_text, write_json
from .urls import UrlError, normalize_channel, normalize_video, video_url

MATERIAL_TYPES = {
    # type: (basis, needs_text, extra requirement)
    "notebooklm_summary": ("AI_SUMMARY", True, "human_reviewed"),
    "manual_notes": ("HUMAN_NOTES", True, None),
    "creator_article": ("CREATOR_ARTICLE", True, "source_url"),
    "authorized_transcript": ("AUTHORIZED_TRANSCRIPT", True, "permission_note"),
    "metadata_only": ("NONE", False, None),
}
COVERAGE = {"FULL", "PARTIAL", "UNKNOWN"}
FIRST_HAND_BASES = {"HUMAN_NOTES", "AUTHORIZED_TRANSCRIPT"}
MAX_TEXT_CHARS = 200_000
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _article_meta(store, key, video_id, source_title, source_published_at, linked_video_url, linked_channel_url):
    meta = {"source_title": (source_title or "").strip() or None, "source_published_at": source_published_at or None,
            "linked_video": None, "linked_channel": None}
    if meta["source_published_at"] and not DATE_RE.fullmatch(meta["source_published_at"]):
        raise StoreError("BAD_DATE", "source_published_at must be YYYY-MM-DD")
    if linked_video_url:
        try:
            vid = normalize_video(linked_video_url)
        except UrlError as e:
            raise StoreError("VIDEO_LINK_MISMATCH", str(e)) from None
        if vid != video_id:
            raise StoreError("VIDEO_LINK_MISMATCH", f"the article links {vid}, not {video_id}")
        meta["linked_video"] = {"video_id": vid, "url": video_url(vid), "basis": "ARTICLE_LINK"}
    if linked_channel_url:
        info = normalize_channel(linked_channel_url)
        ch = store.get_channel(key)
        same_id = bool(info.get("channel_id") and ch.get("channel_id") and info["channel_id"] == ch["channel_id"])
        meta["linked_channel"] = {"url": linked_channel_url.strip(), "handle_matches_registered": info["key"] == ch["key"],
                                  "status": "CHANNEL_ID_VERIFIED" if same_id else "CHANNEL_ID_UNVERIFIED"}
    return meta


def add_material(store, key, video_id, mtype, text=None, source_url=None, human_reviewed=False,
                 permission_note=None, coverage="UNKNOWN", note=None, source_title=None,
                 source_published_at=None, linked_video_url=None, linked_channel_url=None):
    if mtype not in MATERIAL_TYPES:
        raise StoreError("BAD_MATERIAL_TYPE", mtype)
    if coverage not in COVERAGE:
        raise StoreError("BAD_COVERAGE", coverage)
    basis, needs_text, extra = MATERIAL_TYPES[mtype]
    text = (text or "").strip()
    if needs_text and not text:
        raise StoreError("TEXT_REQUIRED", mtype)
    if not needs_text and text:
        raise StoreError("NO_TEXT_FOR_METADATA_ONLY")
    if len(text) > MAX_TEXT_CHARS:
        raise StoreError("TEXT_TOO_LONG", f"{len(text)} chars")
    if extra == "human_reviewed" and not human_reviewed:
        raise StoreError("HUMAN_REVIEW_REQUIRED", "a NotebookLM summary is accepted only after a person checked it")
    if extra == "source_url" and not (source_url or "").startswith(("http://", "https://")):
        raise StoreError("SOURCE_URL_REQUIRED", "creator_article needs the article URL")
    if extra == "permission_note" and not (permission_note or "").strip():
        raise StoreError("NEEDS_POLICY_REVIEW", "authorized_transcript needs a note on who allowed storing/processing it")
    article_args = (source_title, source_published_at, linked_video_url, linked_channel_url)
    if mtype != "creator_article" and any(article_args):
        raise StoreError("ARTICLE_FIELDS_ONLY", "title / date / linked video / linked channel are for creator_article")

    data = store.videos(key)
    v = data["videos"].get(video_id)
    if not v:
        raise StoreError("UNKNOWN_VIDEO", f"{video_id} (register the video URL first)")
    if v.get("kind") == "article":
        if mtype != "creator_article" or source_url != v["url"]:
            raise StoreError("ARTICLE_ITEM_MATERIAL", "an article item takes only its own article (same URL)")
        if linked_video_url:
            raise StoreError("VIDEO_LINK_MISMATCH", "an article item links no video; register the video instead")
    meta = _article_meta(store, key, video_id, *article_args) if mtype == "creator_article" else {}
    digest = sha256_text(f"{mtype}\n{text}\n{source_url or ''}")
    mdir = store.channel_dir(key) / "materials" / video_id
    for mid in v["materials"]:
        m = read_json(mdir / f"{mid}.json")
        if m and m["sha256"] == digest:
            fill = {k: val for k, val in meta.items() if val is not None}
            clash = [k for k, val in fill.items() if m.get(k) is not None and m[k] != val]
            if clash:
                raise StoreError("METADATA_CONFLICT", f"{mid} already has a different {clash}")
            missing = {k: val for k, val in fill.items() if m.get(k) is None}
            if mtype == "creator_article" and "video_directly_analyzed" not in m:
                missing["video_directly_analyzed"] = False      # records made before this field existed
            if missing:
                m.update(missing, metadata_updated_at=store.now())
                write_json(mdir / f"{mid}.json", m)
            return m, False
    mid = f"M{len(v['materials']) + 1:02d}"
    m = {"material_id": mid, "video_id": video_id, "type": mtype, "basis": basis, "coverage": coverage,
         "source_url": source_url, "human_reviewed": bool(human_reviewed), "permission_note": permission_note,
         "note": note, "text": text or None, "sha256": digest, "created_at": store.now()}
    if mtype == "creator_article":
        m.update(meta, video_directly_analyzed=False)
    write_json(mdir / f"{mid}.json", m)
    v["materials"].append(mid)
    v["last_checked_at"] = store.now()
    store.save_videos(key, data)
    return m, True


def load_materials(store, key, video_id):
    data = store.videos(key)
    v = data["videos"].get(video_id) or {"materials": []}
    mdir = store.channel_dir(key) / "materials" / video_id
    return {mid: read_json(mdir / f"{mid}.json") for mid in v["materials"]}
