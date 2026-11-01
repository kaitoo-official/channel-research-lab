"""Channel-wide synthesis over per-video analyses (no model call; deterministic grouping + rules).

- Claims are grouped into themes by `topic_key` (set by the analyst) or, if absent, by text similarity within
  the same category. Duplicates collapse into one theme instead of being counted twice.
- A theme is 'RECURRING' only when claims from >= 2 distinct videos back it, and the theme lists exactly those
  videos. "Many videos say X" is never stated without the list.
- Differing `position` labels inside a theme mark it 'DIVERGENT'; members are ordered by the human-entered
  publish date so a change of view over time is visible (unknown dates are kept last, not guessed).
- Every member carries the materials it came from (`sources`: S1, S2, … numbered across the channel) and its
  three checks, so a report line can be traced back to the article or note behind it.
- A theme needs verification when ANY member is a number/track-record claim, not only the first one.
- Tests carry the principle they point at (`video:P1`); dangling references are listed in `integrity_errors`.
- Items are videos or creator articles that link no video (kind=article). 'RECURRING' needs >= 2 items AND
  >= 2 distinct origins (article URL, or the video itself), so one article repeating a point is one source.
- Members are ordered by the video's human-entered date, else by the article's publish date (`date_basis`).
"""
from .analysis import CRITICAL_CATEGORIES, WEB_KINDS, claim_checks, needs_review, similar
from .materials import load_materials
from .store import read_json, write_json

SIMILARITY = 0.6
BASIS_LABELS = {"CREATOR_ARTICLE": "作者の公式記事", "AI_SUMMARY": "AI 要約", "HUMAN_NOTES": "人の視聴メモ",
                "AUTHORIZED_TRANSCRIPT": "許可済みの字幕", "NONE": "内容なし"}


def _source_table(store, key, analyses):
    """[(video_id, material_id) -> source entry]; numbered in video / material order."""
    table, n = {}, 0
    items = store.videos(key)["videos"]
    for vid in analyses:
        item = items.get(vid) or {}
        mats = load_materials(store, key, vid)
        for mid, m in mats.items():
            if not m:
                continue
            n += 1
            table[(vid, mid)] = {"source_id": f"S{n}", "video_id": vid, "material_id": mid, "type": m["type"],
                                 "item_kind": item.get("kind", "video"), "item_url": item.get("url"),
                                 "same_content_as": item.get("same_content_as"),
                                 "basis": m["basis"], "basis_label": BASIS_LABELS.get(m["basis"], m["basis"]),
                                 "coverage": m["coverage"], "source_url": m.get("source_url"),
                                 "source_title": m.get("source_title"),
                                 "source_published_at": m.get("source_published_at"),
                                 "linked_video": m.get("linked_video"), "linked_channel": m.get("linked_channel"),
                                 "video_directly_analyzed": m["basis"] in ("HUMAN_NOTES", "AUTHORIZED_TRANSCRIPT"),
                                 "human_reviewed": m.get("human_reviewed", False)}
    return table


def _integrity(analyses, sources):
    errs = []
    for vid, a in analyses.items():
        cids = {c["id"] for c in a["claims"]}
        pids = {p["id"] for p in a["principles"]}
        for c in a["claims"]:
            errs += [{"code": "CLAIM_MATERIAL_MISSING", "ref": f"{vid}:{c['id']}", "missing": m}
                     for m in c["material_ids"] if (vid, m) not in sources]
        for p in a["principles"]:
            errs += [{"code": "PRINCIPLE_CLAIM_MISSING", "ref": f"{vid}:{p['id']}", "missing": d}
                     for d in p["derived_from"] if d not in cids]
        for i, t in enumerate(a["tests"]):
            if t["principle_id"] not in pids:
                errs.append({"code": "TEST_PRINCIPLE_MISSING", "ref": f"{vid}:tests[{i}]", "missing": t["principle_id"]})
        for e in a["evidence"]:
            if e["claim_id"] not in cids:
                errs.append({"code": "EVIDENCE_CLAIM_MISSING", "ref": f"{vid}:evidence", "missing": e["claim_id"]})
    return errs


def _analyses(store, key):
    d = store.channel_dir(key) / "analyses"
    return {p.stem: read_json(p) for p in sorted(d.glob("*.json"))} if d.exists() else {}


def coverage(store, key):
    ch = store.get_channel(key)
    allv = store.videos(key)["videos"]
    vids = {k: v for k, v in allv.items() if v.get("kind") != "article"}
    counts = {s: 0 for s in ("ANALYZED", "PARTIAL", "UNANALYZED", "ON_HOLD", "BLOCKED")}
    for v in vids.values():
        counts[v["status"]] += 1
    known = len(vids)
    articles = [v for v in allv.values() if v.get("kind") == "article"]
    # Own data only. The API listing is never stored (retention model A), so it never feeds coverage, synthesis
    # or the saved report. A ratio is shown only for a list a person confirmed as complete.
    complete = ch["video_list"]["complete"]
    basis = "human_confirmed" if complete else None
    rate = round(counts["ANALYZED"] / known, 3) if complete and known else None
    note = None if complete else ("動画一覧が全件だと人が確認していないため、チャンネル全体の網羅率は出さない"
                                  "（API の一覧は保存しない。全件との照合は crl_net.run_sync の実行中の表示だけ）")
    return {"known_videos": known, **{k.lower(): n for k, n in counts.items()},
            "article_items": len(articles), "article_items_analyzed": sum(1 for v in articles if v["analysis"]),
            "video_list_confirmed_complete": complete, "list_basis": basis,
            "channel_coverage_rate": rate, "coverage_note": note}


def synthesize(store, key):
    vids = store.videos(key)["videos"]
    analyses = _analyses(store, key)
    sources = _source_table(store, key, analyses)
    themes = []
    for vid, a in analyses.items():
        mats = load_materials(store, key, vid)
        for c in a["claims"]:
            checks = c.get("checks") or claim_checks(c, a["evidence"], mats)   # records from before Phase 6.3
            refs = [sources[(vid, m)] for m in c["material_ids"] if (vid, m) in sources]
            item = vids.get(vid) or {}
            same = (item.get("same_content_as") or {}).get("video_id")
            vdate = None if item.get("kind") == "article" else \
                (item.get("fields", {}).get("published_at") or {}).get("value")
            adate = min((r["source_published_at"] for r in refs if r["source_published_at"]), default=None)
            member = {"video_id": vid, "claim_id": c["id"], "text": c["text"], "category": c["category"],
                      "position": c.get("position"), "verification": c["verification"],
                      "verification_required": c.get("verification_required", False),
                      "content_check": c["content_check"], "timestamp": c.get("timestamp"),
                      "checks": checks, "needs_review": c.get("needs_review", needs_review(checks)),
                      "sources": [r["source_id"] for r in refs],
                      # one talk = one origin: a video (whatever material describes it), or an article item,
                      # or the video an article item is the text version of
                      "origins": [f"item:{same}"] if same else (
                          sorted({r["source_url"] for r in refs}) or [f"item:{vid}"]
                          if item.get("kind") == "article" else [f"item:{vid}"]),
                      "published_at": vdate or adate,
                      "date_basis": "video" if vdate else ("article" if adate else None)}
            home = None
            for t in themes:
                if c.get("topic_key"):
                    if t["topic_key"] == c["topic_key"]:
                        home = t
                        break
                elif t["topic_key"] is None and t["category"] == c["category"] and \
                        similar(t["members"][0]["text"], c["text"]) >= SIMILARITY:
                    home = t
                    break
            if home is None:
                home = {"theme_id": f"T{len(themes) + 1:03d}", "topic_key": c.get("topic_key"),
                        "category": c["category"], "members": []}
                themes.append(home)
            if not any(m["video_id"] == vid and m["claim_id"] == c["id"] for m in home["members"]):
                home["members"].append(member)

    for t in themes:
        t["members"].sort(key=lambda m: (m["published_at"] is None, m["published_at"] or "", m["video_id"]))
        t["videos"] = sorted({m["video_id"] for m in t["members"]})
        t["origins"] = sorted({o for m in t["members"] for o in m["origins"]})
        t["recurring"] = len(t["videos"]) >= 2 and len(t["origins"]) >= 2
        positions = {m["position"] for m in t["members"] if m["position"]}
        # differing labels inside ONE source are wording, not disagreement: need two sources that differ
        t["divergent"] = any(a["position"] and b["position"] and a["position"] != b["position"]
                             and not set(a["origins"]) & set(b["origins"])
                             for a in t["members"] for b in t["members"])
        t["positions"] = sorted(positions)
        t["confirmed"] = any(m["verification"] == "CONFIRMED" for m in t["members"])
        t["person_checked_videos"] = sorted({m["video_id"] for m in t["members"]
                                             if m["content_check"] == "PERSON_CHECKED"})
        t["needs_verification"] = not t["confirmed"] and any(
            m["category"] in CRITICAL_CATEGORIES or m["verification_required"] for m in t["members"])
        t["needs_review"] = any(m["needs_review"] for m in t["members"])
        t["status"] = "HYPOTHESIS"

    tests = []
    for vid, a in analyses.items():
        principles = {p["id"]: p for p in a["principles"]}
        claims = {c["id"]: c for c in a["claims"]}
        for t in a["tests"]:
            p = principles.get(t["principle_id"])
            basis = [m for th in themes for m in th["members"]
                     if p and m["video_id"] == vid and m["claim_id"] in p["derived_from"]]
            tests.append({"video_id": vid, **t, "principle": None if not p else {
                "ref": f"{vid}:{p['id']}", "statement": p["statement"], "derived_from": p["derived_from"],
                "derived_claims_found": all(d in claims for d in p["derived_from"]),
                "basis_needs_review": sum(m["needs_review"] for m in basis),
                "basis_fact_unverified": sum(m["checks"]["fact"] != "FACT_VERIFIED" for m in basis),
                "basis_video_unverified": sum(m["checks"]["video"] != "VIDEO_VERIFIED" for m in basis)}})
    confirmed, unverified = [], []
    for vid, a in analyses.items():
        for c in a["claims"]:
            refs = [sources[(vid, m)]["source_id"] for m in c["material_ids"] if (vid, m) in sources]
            if c["verification"] == "CONFIRMED":
                urls = sorted({e["source_url"] for e in a["evidence"] if e["claim_id"] == c["id"] and e["supports"]
                               and e["source_url"] and e["kind"] != "SOURCE_TEXT"})
                confirmed.append({"video_id": vid, "claim_id": c["id"], "text": c["text"], "sources": urls,
                                  "material_sources": refs})
            elif c["verification_required"]:
                unverified.append({"video_id": vid, "claim_id": c["id"], "text": c["text"], "material_sources": refs})
    results = []
    for vid, a in analyses.items():
        for c in a["claims"]:
            web = [e for e in a["evidence"] if e["claim_id"] == c["id"] and e["kind"] in WEB_KINDS]
            if not web:
                continue
            downs = [d["reason"] for d in a.get("policy_downgrades", []) if d.get("item") == c["id"] and "to" in d]
            results.append({"video_id": vid, "claim_id": c["id"], "text": c["text"], "final": c["verification"],
                            "material_sources": [sources[(vid, m)]["source_id"] for m in c["material_ids"]
                                                 if (vid, m) in sources],
                            "downgrades": downs,
                            "evidence": [{k: e.get(k) for k in ("kind", "source_url", "official",
                                                                "independent_verification", "supports",
                                                                "supported_part", "unsupported_part", "note")}
                                         for e in web]})
    open_items = []
    for vid, v in sorted(vids.items()):
        if v["status"] in ("UNANALYZED", "ON_HOLD", "BLOCKED"):
            open_items.append({"video_id": vid, "status": v["status"], "reason": v.get("hold_reason")})
        elif v["status"] == "PARTIAL":
            used = sorted({s["basis_label"] for s in sources.values() if s["video_id"] == vid})
            direct = any(s["video_directly_analyzed"] for s in sources.values() if s["video_id"] == vid)
            if v.get("kind") == "article":
                reason = "記事だけの項目（記事に個別の動画リンクがなく、対応する動画は未特定）"
            elif direct:
                reason = "人が動画の一部だけを確認（全体は未確認）"
            else:
                reason = f"動画は直接分析していない。材料は {'・'.join(used) or '不明'} のみ"
            open_items.append({"video_id": vid, "status": "PARTIAL", "reason": reason})
    out = {"generated_at": store.now(), "coverage": coverage(store, key), "themes": themes,
           "sources": sorted(sources.values(), key=lambda s: int(s["source_id"][1:])),
           "integrity_errors": _integrity(analyses, sources),
           # a theme the videos disagree on is not a principle; it is reported under divergent views only
           "recurring_principles": [t["theme_id"] for t in themes if t["recurring"] and not t["divergent"]],
           "divergent_themes": [t["theme_id"] for t in themes if t["divergent"]],
           "verification_candidates": [t["theme_id"] for t in themes if t["needs_verification"]],
           "confirmed_claims": confirmed, "unverified_claims": unverified, "verification_results": results,
           "tests": tests, "open_items": open_items}
    write_json(store.channel_dir(key) / "synthesis.json", out)
    return out
