"""Markdown report from synthesis.json. Short paraphrases only; every statement lists its sources.

Each claim line names the material it came from as [S1], [S2], … and the 出典一覧 section resolves those to the
article title / URL / date or the note behind them. The three checks are shown separately (記事照合 / 動画 /
事実) so a creator article being quoted is never read as the video or the fact being checked.
"""
from .store import read_json
from .urls import video_url

TARGET_LABELS = {"kaitoo-works": "KAITOO Works", "ai-use": "AI活用", "sales-automation": "営業自動化",
                 "client-acquisition": "受託案件の獲得", "small-bet": "Small Bet 型の新規事業"}
SOURCE_LABELS = {"SOURCE_VERIFIED": "記事照合済", "SOURCE_UNCHECKED": "記事未照合",
                 "SOURCE_CONTRADICTED": "記事と不一致", "NOT_APPLICABLE": None}
METHOD_LABELS = {"AI_MEDIATED_FETCH": "AI経由", "DIRECT_TEXT": "原文照合", "HUMAN_READ": "人が確認",
                 "UNSPECIFIED": "方法不明"}
VIDEO_LABELS = {"VIDEO_VERIFIED": "動画確認済", "VIDEO_UNVERIFIED": "動画未確認"}
FACT_LABELS = {"FACT_VERIFIED": "事実確認済", "FACT_UNVERIFIED": "事実未検証"}
_ITEMS = {}


def _src(vids):
    out = []
    for v in vids:
        item = _ITEMS.get(v) or {}
        if item.get("kind") == "article":
            out.append(f"[記事:{v}]({item['url']})")
        else:
            out.append(f"[{v}]({item.get('url') or video_url(v)})")
    return "、".join(out)


def _refs(ids):
    return "".join(f"[{i}]" for i in ids) or "[出典なし]"


def _checks(m):
    c = m["checks"]
    src = SOURCE_LABELS[c["source"]]
    if src and c.get("source_method"):
        src += "（" + "/".join(METHOD_LABELS.get(x, x) for x in c["source_method"].split("/")) + "）"
    parts = [src, VIDEO_LABELS[c["video"]], FACT_LABELS[c["fact"]]]
    return "・".join(p for p in parts if p) + ("・**要確認**" if m["needs_review"] else "")


def _theme_check(t, srcs):
    if t["person_checked_videos"]:
        return "人が動画を確認したものあり"
    kinds = sorted({srcs[s]["basis_label"] for m in t["members"] for s in m["sources"] if s in srcs})
    return f"動画未確認（{'・'.join(kinds) or '出典不明'}のみ）"


def render(store, key):
    ch = store.get_channel(key)
    s = read_json(store.channel_dir(key) / "synthesis.json")
    if not s:
        raise ValueError("run synthesize first")
    cov = s["coverage"]
    themes = {t["theme_id"]: t for t in s["themes"]}
    srcs = {x["source_id"]: x for x in s.get("sources", [])}
    _ITEMS.clear()
    _ITEMS.update(store.videos(key)["videos"])
    L = [f"# Channel Research Lab｜チャンネル研究レポート：{ch.get('handle') or ch.get('channel_id')}",
         "", f"- チャンネル：{ch['canonical_url']}", f"- 作成：{s['generated_at']}",
         "- 位置づけ：人が用意した材料（メモ・確認済みの要約・記事・許可のある字幕）だけからの整理。原則はすべて HYPOTHESIS。",
         "- 確認の表示：記事照合＝作者の記事に書いてあると確かめた／動画＝人が動画で確かめた／事実＝独立した情報源で裏付けた（3つは別物）",
         "", "## 網羅状況",
         f"- 把握している動画：{cov['known_videos']} 本（人が動画全体を確認 {cov['analyzed']}／"
         f"動画の全体確認なし（記事・AI 要約・一部のみ） {cov['partial']}／"
         f"未分析 {cov['unanalyzed']}／保留 {cov['on_hold']}／分析不可 {cov['blocked']}）",
         f"- 動画リンクのない作者記事：{cov.get('article_items', 0)} 本（分析済み {cov.get('article_items_analyzed', 0)}。動画の本数には数えない）",
         f"- チャンネル全体の網羅率：{cov['channel_coverage_rate'] if cov['channel_coverage_rate'] is not None else 'UNKNOWN'}"
         + (f"（{cov['coverage_note']}）" if cov["coverage_note"] else ""), ""]

    errs = s.get("integrity_errors") or []
    if errs:
        L += ["## 参照の不整合（直すまで、該当する行は信頼しない）"]
        L += [f"- {e['code']}：{e['ref']} が {e['missing']} を参照しているが見つからない" for e in errs] + [""]

    # Saved reports contain own data only: no API value is ever stored (retention model A).
    L += ["## 知識体系（テーマ別）"]
    for cat in sorted({t["category"] for t in themes.values()}):
        L.append(f"### {cat}")
        for t in themes.values():
            if t["category"] != cat:
                continue
            tag = ("繰り返し・見解が分かれる" if t["divergent"] else "繰り返し") if t["recurring"] else "単発"
            L.append(f"- **{t['theme_id']}**（{tag}・{_theme_check(t, srcs)}・"
                     f"{'外部検証済み' if t['confirmed'] else '未検証'}）　動画：{_src(t['videos'])}")
            for m in t["members"]:
                L.append(f"  - {m['claim_id']}{_refs(m['sources'])}：{m['text']}（{_checks(m)}）")
        L.append("")

    L += ["## 繰り返し語られる原則（2本以上の別々の出典で確認・HYPOTHESIS）"]
    for tid in s["recurring_principles"]:
        t = themes[tid]
        origins = t.get("origins") or t["videos"]
        L.append(f"- {tid}：{t['members'][0]['text']}（別々の出典 {len(origins)} 件：{_src(t['videos'])}）")
        L += [f"  - {m['claim_id']}{_refs(m['sources'])} {m['video_id']}：{m['text']}（{_checks(m)}）" for m in t["members"]]
    if not s["recurring_principles"]:
        L.append("- なし（2本以上の別々の出典で確認できた主張はまだない）")
    L += ["", "## 矛盾・見解の変化"]
    for tid in s["divergent_themes"]:
        t = themes[tid]
        L.append(f"- {tid}：立場 {' / '.join(t['positions'])}")
        for m in t["members"]:
            basis = {"video": "動画の公開日", "article": "記事の公開日"}.get(m.get("date_basis"), "")
            L.append(f"  - {m['published_at'] or '公開日不明'}{('（' + basis + '）') if basis else ''} "
                     f"{m['video_id']}{_refs(m['sources'])}：{m['position'] or '-'}：{m['text']}")
    if not s["divergent_themes"]:
        L.append("- なし")
    L += ["", "## 時系列（出典の公開日順。動画の公開日が分からないものは記事の公開日）"]
    seen = {}
    for x in s.get("sources", []):
        d = x.get("source_published_at")
        seen.setdefault(x["video_id"], (d, x))
    for vid, (d, x) in sorted(seen.items(), key=lambda kv: (kv[1][0] is None, kv[1][0] or "", kv[0])):
        n = sum(1 for t in themes.values() for m in t["members"] if m["video_id"] == vid)
        L.append(f"- {d or '日付不明'}：{_src([vid])}『{x.get('source_title') or '（タイトル未記録）'}』（主張 {n} 件）")
    if not seen:
        L.append("- なし")
    L += ["", "## 外部で検証すべき主張（数字・規約・実績など）"]
    for tid in s["verification_candidates"]:
        t = themes[tid]
        L.append(f"- {tid}（動画：{_src(t['videos'])}）")
        L += [f"  - {m['claim_id']}{_refs(m['sources'])}：{m['text']}" for m in t["members"]]
    if not s["verification_candidates"]:
        L.append("- なし")
    L += ["", "## 外部検証の結果（どこまで裏付けられたか）"]
    for r in s.get("verification_results", []):
        L.append(f"- {r['claim_id']}{_refs(r['material_sources'])}（{_src([r['video_id']])}）：{r['text']}")
        for e in r["evidence"]:
            kind = "独立" if e["independent_verification"] else "自己申告・非独立"
            sup = {True: "裏付けあり", False: "否定", None: "裏付けなし"}[e["supports"]]
            L.append(f"  - 根拠：{e['source_url']}（{e['kind']}・{kind}・{sup}）")
            if e.get("supported_part"):
                L.append(f"    - 裏付けられた範囲：{e['supported_part']}")
            if e.get("unsupported_part"):
                L.append(f"    - 裏付けられなかった部分：{e['unsupported_part']}")
        status = "CONFIRMED（事実確認済）" if r["final"] == "CONFIRMED" else "UNVERIFIED（事実未検証）"
        L.append(f"  - 最終判定：{status}" + (f"（{'／'.join(r['downgrades'])}）" if r["downgrades"] else ""))
    if not s.get("verification_results"):
        L.append("- なし")
    L += ["", "## 確認済みの根拠（独立した一次情報・公式データで確認できた主張）"]
    L += [f"- {c['claim_id']}{_refs(c.get('material_sources', []))}：{c['text']}"
          f"（動画：{_src([c['video_id']])}／根拠：{'、'.join(c['sources'])}）"
          for c in s.get("confirmed_claims", [])] or ["- なし"]
    L += ["", "## 未検証の主張（数字・実績・規約など。検証するまで事実として扱わない）"]
    L += [f"- {c['claim_id']}{_refs(c.get('material_sources', []))}：{c['text']}（動画：{_src([c['video_id']])}）"
          for c in s.get("unverified_claims", [])] or ["- なし"]

    L += ["", "## 事業で試せるテスト案（PROPOSED・採否は各事業のセッションで本人が判断）"]
    for t in s["tests"]:
        L.append(f"- [{TARGET_LABELS.get(t['target'], t['target'])}] 変数：{t['variable']}／仮説：{t['hypothesis']}／"
                 f"指標：{t['metric']}／成功：{t['success']}／中止：{t['stop_rule']}")
        p = t.get("principle")
        if p:
            n = len(p["derived_from"])
            L.append(f"  - 根拠の原則 {p['ref']}（HYPOTHESIS）：{p['statement']}　元の主張：{'・'.join(p['derived_from'])}"
                     f"（{n}件中 動画未確認 {p['basis_video_unverified']}・事実未検証 {p['basis_fact_unverified']}"
                     f"・要確認 {p['basis_needs_review']}）")
        else:
            L.append(f"  - 根拠の原則 {t['video_id']}:{t['principle_id']} が見つからない（参照の不整合）")
    if not s["tests"]:
        L.append("- なし")
    L += ["", "## 未分析・未確認"]
    L += [f"- {o['video_id']}：{o['status']}" + (f"（{o['reason']}）" if o["reason"] else "") for o in s["open_items"]] \
        or ["- なし"]

    L += ["", "## 出典一覧（材料）"]
    for x in s.get("sources", []):
        head = f"- [{x['source_id']}] {x['type']}（{x['basis_label']}）"
        if x["type"] == "creator_article":
            head += f"：『{x['source_title'] or 'タイトル未記録'}』 {x['source_url']}（公開日 {x['source_published_at'] or '未記録'}）"
        L.append(head)
        lv = x.get("linked_video")
        sc = x.get("same_content_as")
        if x.get("item_kind") == "article" and sc:
            L.append(f"  - 記事だけの項目：同じ話の動画とみなす → {video_url(sc['video_id'])}（根拠 {sc['basis']}。"
                     f"{'記事内のリンク' if sc['basis'] == 'ARTICLE_LINK' else 'タイトルの一致による推定'}。共通原則の数え方では 1 出典として扱う）")
        elif x.get("item_kind") == "article":
            L.append("  - 記事だけの項目：記事内に個別の動画リンクがない（対応する動画は未特定。動画は分析していない）")
        elif lv:
            L.append(f"  - 記事から参照される動画：{lv['url']}（記事内のリンクで確認。動画は直接分析していない）")
        else:
            L.append(f"  - 対象の動画：{video_url(x['video_id'])}"
                     + ("" if x["video_directly_analyzed"] else "（動画は直接分析していない）"))
        lc = x.get("linked_channel")
        if lc:
            L.append(f"  - 記事が示すチャンネル：{lc['url']}　{lc['status']}"
                     f"（ハンドル表記の一致：{'あり' if lc['handle_matches_registered'] else 'なし'}。表記だけでは同じチャンネルと判断しない）")
        elif x["type"] == "creator_article":
            L.append("  - 記事が示すチャンネル：記事内にチャンネルへのリンクなし　CHANNEL_ID_UNVERIFIED"
                     "（記事の動画が登録チャンネルのものかは未確認）")
    if not s.get("sources"):
        L.append("- なし")

    L += ["", "## 確認済みの動画ソース（人が動画を確認したもの）"]
    vids = store.videos(key)["videos"]
    checked = {x["video_id"] for x in s.get("sources", []) if x["video_directly_analyzed"]}
    rows = []
    for vid, v in sorted(vids.items()):
        if v["analysis"] and vid in checked:
            title = (v["fields"].get("title") or {}).get("value") or "（タイトル未入力）"
            rows.append(f"- {video_url(vid)}　{title}　状態：{v['status']}")
    L += rows or ["- なし（人が動画を確認したものはまだない）"]
    return "\n".join(L) + "\n"
