"""YouTube Data API v3 — upload listing for ONE channel (logic only; NO network code in this package).

The HTTP transport is injected: `transport(endpoint, params) -> dict`. The real transport lives outside `ytca/`
(`crl_net/transport.py`) and may be used only after owner approval (docs/api_design.md).

Retention model A (2026-10-27, owner-approved; docs/api_retention_decision.md):
- API data (video IDs, titles, publish dates, channel ID, uploads playlist ID, page tokens) is held IN MEMORY for
  one owner-started run and then dropped. `fetch_listing` has no store argument: it cannot write anything.
- No checkpoint: a run stopped by the budget is simply incomplete; the next run fetches again from the start.
- `listing_view` shows the API rows as returned and the API's own pageInfo.totalResults. It does NOT count,
  filter or compare API rows against the owner's records: permission for such derived data is not confirmed
  (III.E.4.h / III.L -> NEEDS_POLICY_REVIEW, docs/api_terms_review_2026-10-27.md). Own-record counts are shown in
  a separate block with a "not from YouTube" disclosure.
- The owner's own records (videos.json, materials, analyses) are created only by the owner's own actions
  (channel-add / videos-add / material-add / analysis-import). Nothing here copies API values into them.
- `purge_api_data` removes anything a pre-A version stored (api_listing.json, API-sourced video fields/records).
Two endpoints with fixed parts: channels.list(part=id,contentDetails) and playlistItems.list(part=contentDetails,snippet).
Hard budget of MAX_BUDGET units per run. No automatic retries, no unattended loops. No statistics, ranking or scores.
"""
import math
import re

from .store import StoreError, now_iso, read_json, write_json
from .urls import CHANNEL_ID_RE, VIDEO_ID_RE

ENDPOINTS = {
    "channels.list": {"part": "id,contentDetails", "cost": 1},
    "playlistItems.list": {"part": "contentDetails,snippet", "cost": 1},
}
PAGE_SIZE = 50
MAX_BUDGET = 20                       # 1 + 19 pages x 50 = up to 950 uploads in one run
UPLOADS_RE = re.compile(r"^UU[A-Za-z0-9_-]{22}$")
SOURCE_LABEL = "YouTube Data API v3"
ATTRIBUTION = ("出典：YouTube Data API（YouTube 利用規約 https://www.youtube.com/t/terms ・ "
               "Google プライバシーポリシー https://policies.google.com/privacy）。この一覧は保存しない（この実行中の表示だけ）")
LEGACY_LISTING = "api_listing.json"


class ApiError(StoreError):
    pass


class Budget:
    def __init__(self, units):
        if not isinstance(units, int) or not 1 <= units <= MAX_BUDGET:
            raise ApiError("BAD_BUDGET", f"budget must be 1..{MAX_BUDGET} units")
        self.units, self.used = units, 0

    def can_spend(self, cost):
        return self.used + cost <= self.units

    def spend(self, cost):
        self.used += cost


def estimate_units(video_count, resolve_handle=True):
    """Units for one full listing: 1 (channel) + 1 per 50 uploads."""
    return (1 if resolve_handle else 0) + max(1, math.ceil(video_count / PAGE_SIZE))


def _call(transport, budget, endpoint, **params):
    spec = ENDPOINTS[endpoint]
    budget.spend(spec["cost"])
    try:
        resp = transport(endpoint, {"part": spec["part"], **params})
    except ApiError:
        raise
    except Exception as e:   # the transport never puts the key or URL in its errors
        raise ApiError("API_TRANSPORT_ERROR", type(e).__name__) from None
    if not isinstance(resp, dict):
        raise ApiError("API_BAD_RESPONSE", endpoint)
    if "error" in resp:
        err = resp["error"] or {}
        reason = ((err.get("errors") or [{}])[0]).get("reason") or str(err.get("code", "unknown"))
        raise ApiError("API_ERROR", f"{endpoint}: {reason}")
    return resp


def resolve_channel(transport, budget, channel):
    params = {"id": channel["channel_id"]} if channel.get("channel_id") else {"forHandle": channel["handle"]}
    items = _call(transport, budget, "channels.list", **params).get("items") or []
    if len(items) != 1:
        raise ApiError("CHANNEL_NOT_FOUND", channel.get("handle") or channel.get("channel_id"))
    cid = items[0].get("id")
    uploads = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not (isinstance(cid, str) and CHANNEL_ID_RE.fullmatch(cid) and isinstance(uploads, str)
            and UPLOADS_RE.fullmatch(uploads)):
        raise ApiError("API_BAD_RESPONSE", "channel id / uploads playlist")
    return cid, uploads


def _parse_items(resp):
    items = resp.get("items")
    if items is not None and not isinstance(items, list):
        raise ApiError("API_BAD_RESPONSE", "playlistItems.items")
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        cd, sn = it.get("contentDetails") or {}, it.get("snippet") or {}
        vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId")
        if not (isinstance(vid, str) and VIDEO_ID_RE.fullmatch(vid)):
            continue
        pub = cd.get("videoPublishedAt")
        pub = pub[:10] if isinstance(pub, str) and pub else None   # absent for private/deleted entries
        title = sn.get("title")
        if pub is None or title in ("Private video", "Deleted video"):
            continue
        out.append({"video_id": vid, "title": title if isinstance(title, str) else None, "published_at": pub})
    return out


def fetch_listing(transport, channel, budget_units=MAX_BUDGET, clock=None):
    """One bounded listing held in memory. Returns {result: COMPLETE|INCOMPLETE_BUDGET, items, units_used, ...}.
    Writes nothing (no store argument). INCOMPLETE_BUDGET is not resumable on purpose: run again later."""
    budget = Budget(budget_units)
    _, uploads = resolve_channel(transport, budget, channel)
    items, seen, token, pages, total = [], set(), None, 0, None
    while True:
        if not budget.can_spend(1):
            return {"result": "INCOMPLETE_BUDGET", "complete": False, "items": items, "pages": pages,
                    "api_total_results": total, "units_used": budget.used, "fetched_at": now_iso(clock),
                    "source": SOURCE_LABEL}
        params = {"playlistId": uploads, "maxResults": PAGE_SIZE}
        if token:
            params["pageToken"] = token
        resp = _call(transport, budget, "playlistItems.list", **params)
        if total is None:
            t = (resp.get("pageInfo") or {}).get("totalResults") if isinstance(resp.get("pageInfo"), dict) else None
            total = t if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else None
        for it in _parse_items(resp):
            if it["video_id"] not in seen:
                seen.add(it["video_id"])
                items.append(it)
        pages += 1
        token = resp.get("nextPageToken")
        if token is not None and not isinstance(token, str):
            raise ApiError("API_BAD_RESPONSE", "nextPageToken")
        if not token:
            return {"result": "COMPLETE", "complete": True, "items": items, "pages": pages,
                    "api_total_results": total, "units_used": budget.used, "fetched_at": now_iso(clock),
                    "source": SOURCE_LABEL}


def listing_view(store, key, listing, show="none", limit=50):
    """Display-only. API block: the rows exactly as returned (ID / title / publish date) and the API's own
    pageInfo.totalResults - no count, filter, join or comparison made by us. Own block: counts of the owner's
    own records, labelled as not from YouTube. Writes nothing. show: none (no rows) | all (rows up to limit)."""
    if show not in ("none", "all"):
        raise ApiError("BAD_SHOW", f"{show} (only none / all; comparisons with own records are on hold)")
    own = {k: v for k, v in store.videos(key)["videos"].items() if v.get("kind") != "article"}
    view = {"api": {"result": listing["result"], "complete": listing["complete"],
                    "total_results_reported_by_api": listing.get("api_total_results"),
                    "fetched_at": listing["fetched_at"], "attribution": ATTRIBUTION,
                    "note": ("" if listing["complete"] else "一覧は途中まで（予算で停止）。全件ではない。")},
            "own_records": {"disclosure": "この欄は自分の記録から数えた数字で、YouTube のデータではない",
                            "videos": len(own),
                            "analyzed": sum(1 for v in own.values() if v["status"] == "ANALYZED"),
                            "with_material_or_analysis": sum(1 for v in own.values() if v["materials"] or v["analysis"])},
            "run": {"units_used": listing["units_used"], "pages": listing["pages"]}}
    if show == "all":
        rows = listing["items"]
        view["api"]["rows"] = [dict(r) for r in rows[:limit]]
        view["api"]["rows_truncated"] = len(rows) > limit
    return view


def purge_api_data(store, key=None):
    """Remove API data stored by pre-A versions: channels/*/api_listing.json, API-sourced fields / sources /
    flags in videos.json, and video records that exist ONLY because of the API (no human source, material or
    analysis). Human records, materials and analyses are never touched. Local only; safe to run any time."""
    root = store.root / "channels"
    keys = [key] if key else ([p.name for p in root.iterdir() if p.is_dir()] if root.exists() else [])
    out = {"listing_files_removed": 0, "api_fields_removed": 0, "api_only_videos_removed": []}
    for k in keys:
        d = store.channel_dir(k)
        legacy = d / LEGACY_LISTING
        if legacy.exists():
            legacy.unlink()
            out["listing_files_removed"] += 1
        data = read_json(d / "videos.json")
        if not data:
            continue
        changed = False
        for vid in list(data["videos"]):
            v = data["videos"][vid]
            for name in [n for n, f in v["fields"].items() if f.get("source") == "api"]:
                del v["fields"][name]
                out["api_fields_removed"] += 1
                changed = True
            if any(s.get("type") == "api" for s in v["sources"]) or {"api_listed", "api_note",
                                                                      "id_retained_reason"} & set(v):
                v["sources"] = [s for s in v["sources"] if s.get("type") != "api"]
                for f in ("api_listed", "api_note", "id_retained_reason"):
                    v.pop(f, None)
                changed = True
                if not (v["sources"] or v["materials"] or v["analysis"]):
                    del data["videos"][vid]
                    out["api_only_videos_removed"].append(vid)
        if changed:
            write_json(d / "videos.json", data)
    return out
