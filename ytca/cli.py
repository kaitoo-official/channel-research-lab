"""python -m ytca [--root DIR] <command> ...   (local files only; no network)"""
import argparse
import json
import sys
from pathlib import Path

from . import api, batch, materials, notebooklm, report, synthesis
from .analysis import import_analysis
from .store import Store, StoreError, write_json
from .urls import UrlError

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "data"


def _out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp932
    ap = argparse.ArgumentParser(prog="ytca")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("channel-add"); s.add_argument("url"); s.add_argument("--title")
    s = sub.add_parser("videos-add"); s.add_argument("channel"); s.add_argument("file", help="url<TAB>title<TAB>date")
    s.add_argument("--note")
    s = sub.add_parser("article-add", help="a creator article that links no specific video")
    s.add_argument("channel"); s.add_argument("url"); s.add_argument("--title"); s.add_argument("--date")
    s.add_argument("--note")
    s = sub.add_parser("same-content", help="an article item is the text version of a registered video")
    s.add_argument("channel"); s.add_argument("item"); s.add_argument("video")
    s.add_argument("--basis", required=True, choices=("ARTICLE_LINK", "TITLE_MATCH")); s.add_argument("--note")
    s = sub.add_parser("confirm-list"); s.add_argument("channel"); s.add_argument("--by", required=True)
    s.add_argument("--note", required=True)
    s = sub.add_parser("material-add"); s.add_argument("channel"); s.add_argument("video")
    s.add_argument("type", choices=sorted(materials.MATERIAL_TYPES)); s.add_argument("--file")
    s.add_argument("--source-url"); s.add_argument("--human-reviewed", action="store_true")
    s.add_argument("--permission-note"); s.add_argument("--coverage", default="UNKNOWN"); s.add_argument("--note")
    s.add_argument("--source-title", help="creator_article: article title as shown on the page")
    s.add_argument("--source-date", help="creator_article: article publish date YYYY-MM-DD")
    s.add_argument("--linked-video-url", help="creator_article: the video URL exactly as the article links it")
    s.add_argument("--linked-channel-url", help="creator_article: the channel URL exactly as the article links it")
    s = sub.add_parser("analysis-import"); s.add_argument("channel"); s.add_argument("video"); s.add_argument("file")
    s = sub.add_parser("status-set"); s.add_argument("channel"); s.add_argument("video"); s.add_argument("status")
    s.add_argument("--reason")
    s = sub.add_parser("run-manifest"); s.add_argument("manifest")
    for name in ("coverage", "synthesize", "report", "list"):
        sub.add_parser(name).add_argument("channel")
    s = sub.add_parser("expire-api", help="remove any stored API data (legacy cleanup; nothing new is stored)")
    s.add_argument("channel"); s.add_argument("--days", type=int, default=0, help="ignored (kept for old docs)")
    s = sub.add_parser("notebooklm-split"); s.add_argument("channel"); s.add_argument("file")
    s.add_argument("--human-reviewed", action="store_true"); s.add_argument("--coverage", default="UNKNOWN")
    s = sub.add_parser("api-plan"); s.add_argument("videos", type=int, help="expected number of uploads")
    s = sub.add_parser("api-sync"); s.add_argument("channel")
    a = ap.parse_args(argv)
    st = Store(a.root)
    try:
        api.purge_api_data(st)      # every start: nothing API-derived from an older version stays on disk
        if a.cmd == "channel-add":
            ch, new = st.add_channel(a.url, a.title)
            _out({"result": "ADDED" if new else "EXISTS", "channel": ch})
        elif a.cmd == "videos-add":
            added, known = st.add_videos(a.channel, batch.parse_video_list(Path(a.file).read_text(encoding="utf-8")),
                                         note=a.note)
            _out({"result": "OK", "added": added, "known": known})
        elif a.cmd == "article-add":
            item_id, new = st.add_article_item(a.channel, a.url, a.title, a.date, a.note)
            _out({"result": "ADDED" if new else "EXISTS", "item_id": item_id})
        elif a.cmd == "same-content":
            _out({"result": "OK", "same_content_as": st.link_same_content(a.channel, a.item, a.video, a.basis, a.note)})
        elif a.cmd == "confirm-list":
            _out({"result": "OK", "video_list": st.confirm_video_list(a.channel, a.by, a.note)["video_list"]})
        elif a.cmd == "material-add":
            text = Path(a.file).read_text(encoding="utf-8") if a.file else None
            m, new = materials.add_material(st, a.channel, a.video, a.type, text, a.source_url, a.human_reviewed,
                                            a.permission_note, a.coverage, a.note, a.source_title, a.source_date,
                                            a.linked_video_url, a.linked_channel_url)
            _out({"result": "ADDED" if new else "DUPLICATE", "material_id": m["material_id"], "basis": m["basis"],
                  **{k: m[k] for k in ("source_title", "source_published_at", "linked_video", "linked_channel",
                                       "metadata_updated_at") if m.get(k) is not None}})
        elif a.cmd == "analysis-import":
            r = import_analysis(st, a.channel, a.video, json.loads(Path(a.file).read_text(encoding="utf-8")))
            _out({"result": "IMPORTED", "claims": len(r["claims"]), "policy_downgrades": r["policy_downgrades"]})
        elif a.cmd == "status-set":
            st.set_status(a.channel, a.video, a.status, a.reason)
            _out({"result": "OK"})
        elif a.cmd == "run-manifest":
            _out(batch.run_manifest(st, a.manifest))
        elif a.cmd == "coverage":
            _out(synthesis.coverage(st, a.channel))
        elif a.cmd == "synthesize":
            s = synthesis.synthesize(st, a.channel)
            _out({"result": "OK", "themes": len(s["themes"]), "recurring": s["recurring_principles"],
                  "divergent": s["divergent_themes"], "verification_candidates": s["verification_candidates"]})
        elif a.cmd == "report":
            md = report.render(st, a.channel)
            p = st.channel_dir(a.channel) / "report.md"
            p.write_text(md, encoding="utf-8")
            _out({"result": "OK", "report_path": str(p)})
        elif a.cmd == "list":
            _out(st.videos(a.channel))
        elif a.cmd == "expire-api":
            st.get_channel(a.channel)
            _out({"result": "OK", **api.purge_api_data(st, a.channel)})
        elif a.cmd == "notebooklm-split":
            _out({"result": "OK", **notebooklm.import_split(st, a.channel, Path(a.file).read_text(encoding="utf-8"),
                                                            a.human_reviewed, a.coverage)})
        elif a.cmd == "api-plan":
            full = api.estimate_units(a.videos)
            _out({"result": "OK", "units_full_sync": full, "daily_quota_default": 10000,
                  "note": "estimate only; no request is made"})
        elif a.cmd == "api-sync":
            # No transport ships with this package. Connecting needs owner approval (docs/api_design.md).
            _out({"result": "ERROR", "error": "API_NOT_APPROVED: ytca never connects. After owner approval use "
                                                "`python -m crl_net.run_sync <channel> --i-approve-network` (docs/api_design.md)"})
            return 2
    except (StoreError, UrlError) as e:
        _out({"result": "ERROR", "error": str(e)})
        return 2
    return 0
