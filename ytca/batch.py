"""Resumable batch intake from a manifest (local files only).

manifest.json:
  {"channel_url": "...", "items": [
     {"kind": "videos", "file": "videos.tsv"},                        # url<TAB>title<TAB>YYYY-MM-DD per line
     {"kind": "material", "video": "<id>", "type": "manual_notes", "file": "m.md", "coverage": "FULL", ...},
     {"kind": "analysis", "video": "<id>", "file": "a.json"}]}
Each finished item is written to journal.json (key = position + kind + video + content hash). Re-running the
same manifest skips finished items and resumes at the first unfinished one. A failing item stops the run.
"""
import json
from pathlib import Path

from .analysis import import_analysis
from .materials import add_material
from .store import StoreError, sha256_text


def parse_video_list(text):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = [c.strip() for c in line.split("\t")]
        out.append({"url": cols[0], "title": cols[1] if len(cols) > 1 else None,
                    "published_at": cols[2] if len(cols) > 2 else None, "line": n})
    return out


def _read(base, rel):
    p = (base / rel).resolve()
    if base.resolve() not in p.parents:
        raise StoreError("PATH_OUTSIDE_MANIFEST", rel)
    return p.read_text(encoding="utf-8")


def run_manifest(store, manifest_path):
    mpath = Path(manifest_path)
    base = mpath.parent
    m = json.loads(mpath.read_text(encoding="utf-8"))
    ch, _ = store.add_channel(m["channel_url"], title=m.get("channel_title"))
    key = ch["key"]
    report = {"channel": key, "done": 0, "skipped": 0, "failed": None}
    with store.lock(key):
        journal = store.journal(key)
        for i, item in enumerate(m["items"]):
            content = _read(base, item["file"]) if item.get("file") else ""
            jkey = f"{i:04d}:{item['kind']}:{item.get('video', '-')}:{sha256_text(content)[:16]}"
            if jkey in journal["done"]:
                report["skipped"] += 1
                continue
            try:
                if item["kind"] == "videos":
                    store.add_videos(key, parse_video_list(content), note=item.get("note"))
                elif item["kind"] == "material":
                    add_material(store, key, item["video"], item["type"], text=content or None,
                                 source_url=item.get("source_url"), human_reviewed=item.get("human_reviewed", False),
                                 permission_note=item.get("permission_note"), coverage=item.get("coverage", "UNKNOWN"),
                                 note=item.get("note"))
                elif item["kind"] == "analysis":
                    import_analysis(store, key, item["video"], json.loads(content))
                elif item["kind"] == "status":
                    store.set_status(key, item["video"], item["status"], item.get("reason"))
                else:
                    raise StoreError("UNKNOWN_ITEM_KIND", item["kind"])
            except (StoreError, ValueError, KeyError) as e:
                journal["last_failure"] = {"item": i, "key": jkey, "error": str(e), "at": store.now()}
                store.save_journal(key, journal)
                report["failed"] = {"item": i, "error": str(e)}
                return report
            journal["done"][jkey] = store.now()
            journal.pop("last_failure", None)
            store.save_journal(key, journal)
            report["done"] += 1
    return report
