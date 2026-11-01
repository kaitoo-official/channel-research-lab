"""Per-video CLAIM / EVIDENCE / PRINCIPLE / TEST validation.

Same contract as KAITOO Control /yt-analyze (`kaitoo/research.py` validate_and_enforce): model output is not
trusted; CONFIRMED needs an independent primary source or official public data (a creator's own pages never
confirm); PRINCIPLE stays HYPOTHESIS; at most 3 PROPOSED tests with exactly one variable and a kept_constant
list; at most 5 web-verified claims; no copy of >= 60 consecutive characters of the material.
Added for channel work: every claim must point at the material it came from, and its 'basis' records whether
a person checked the video (HUMAN_NOTES / AUTHORIZED_TRANSCRIPT) or only an AI summary exists.
Three checks are kept apart on every claim (`checks`), next to the older CONFIRMED / UNVERIFIED field:
  source  SOURCE_VERIFIED when a SOURCE_TEXT evidence shows the cited creator article really says it
  video   VIDEO_VERIFIED only when a person checked the video (first-hand material)
  fact    FACT_VERIFIED only when `verification` is CONFIRMED (independent source; never the article itself)
A claim with neither a source check nor a video check is `needs_review`.
SOURCE_TEXT must say how the article was compared (`method`): HUMAN_READ, DIRECT_TEXT (the analyst compared the
original text itself) or AI_MEDIATED_FETCH (through a fetch tool that summarises the page with a model).
Web evidence may record `supported_part` / `unsupported_part`; a claim whose only support leaves part of it
unsupported is never CONFIRMED (PARTIAL_SUPPORT).
"""
import difflib
import re
import unicodedata
import urllib.parse

from .materials import FIRST_HAND_BASES, load_materials
from .store import StoreError, write_json

PRESENTATIONS = {"STATED", "SHOWN", "OPINION"}
CLAIM_VERIFICATION = {"UNVERIFIED", "CONFIRMED"}
EVIDENCE_KINDS = {"SPOKEN", "ON_SCREEN", "PUBLIC_DATA", "PRIMARY_SOURCE", "THIRD_PARTY", "OWN_TEST", "SOURCE_TEXT"}
WEB_KINDS = {"PUBLIC_DATA", "PRIMARY_SOURCE", "THIRD_PARTY"}
SOURCE_METHODS = {"HUMAN_READ", "DIRECT_TEXT", "AI_MEDIATED_FETCH"}
CRITICAL_CATEGORIES = {
    "revenue", "price", "success_rate", "market_size", "user_count", "duration", "product_spec",
    "saas_spec", "platform_spec", "law", "terms", "algorithm", "customer_outcome",
    "creator_track_record", "work_time", "reduction_rate"}
CATEGORIES = CRITICAL_CATEGORIES | {"method", "opinion", "other"}
TARGETS = {"kaitoo-works", "ai-use", "sales-automation", "client-acquisition", "small-bet"}
TIMESTAMP_RE = re.compile(r"^\d{1,2}(:\d{2}){1,2}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
MULTI_VARIABLE_HARD = re.compile(r"[、,，/／＋+&＆]|および|及び|並びに|ならびに|\band\b", re.I)
QUOTE_WINDOW = 60
MAX_VERIFICATIONS = 5
MAX_TESTS = 3
MAX_FIELD_CHARS = 2000


def norm_ws(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def _shingles(texts, n=QUOTE_WINDOW):
    out = set()
    for t in texts:
        t = norm_ws(t)
        out.update(t[i:i + n] for i in range(0, max(0, len(t) - n + 1)))
    return out


def _has_long_quote(s, shingles, n=QUOTE_WINDOW):
    s = norm_ws(s)
    return any(s[i:i + n] in shingles for i in range(0, max(0, len(s) - n + 1)))


def _account_key(url):
    u = urllib.parse.urlsplit(url or "")
    host = (u.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    first = next((p for p in u.path.split("/") if p), "")
    return f"{host}/{first.lower()}"


def _s(v, name, required=True, max_chars=MAX_FIELD_CHARS):
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise StoreError("SCHEMA_INVALID", f"{name} is required")
        return None
    if not isinstance(v, str) or len(v) > max_chars:
        raise StoreError("SCHEMA_INVALID", f"{name} must be text <= {max_chars} chars")
    return v.strip()


def _l(v, name, max_len):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > max_len:
        raise StoreError("SCHEMA_INVALID", f"{name} must be a list <= {max_len}")
    return v


def _b(v, name, default=False):
    if v is None:
        return default
    if not isinstance(v, bool):
        raise StoreError("SCHEMA_INVALID", f"{name} must be true/false")
    return v


def claim_checks(claim, evidence, materials):
    """{source, video, fact} for one claim (also used for records stored before `checks` existed)."""
    articles = {(materials.get(m) or {}).get("source_url") for m in claim["material_ids"]
                if (materials.get(m) or {}).get("type") == "creator_article"}
    hits = [e for e in evidence if e["claim_id"] == claim["id"] and e["kind"] == "SOURCE_TEXT"
            and e.get("source_url") in articles]
    found = [e["supports"] for e in hits]
    if not articles:
        source = "NOT_APPLICABLE"
    elif True in found:
        source = "SOURCE_VERIFIED"
    elif False in found:
        source = "SOURCE_CONTRADICTED"
    else:
        source = "SOURCE_UNCHECKED"
    methods = sorted({e.get("method") or "UNSPECIFIED" for e in hits if e["supports"] is (source == "SOURCE_VERIFIED")})
    return {"source": source, "source_method": "/".join(methods) if methods else None,
            "video": "VIDEO_VERIFIED" if claim["content_check"] == "PERSON_CHECKED" else "VIDEO_UNVERIFIED",
            "fact": "FACT_VERIFIED" if claim["verification"] == "CONFIRMED" else "FACT_UNVERIFIED"}


def needs_review(checks):
    if checks["source"] == "SOURCE_CONTRADICTED":
        return True
    return checks["source"] != "SOURCE_VERIFIED" and checks["video"] != "VIDEO_VERIFIED"


def validate(result, materials, creator_urls=()):
    """materials: {material_id: material}. Returns (clean, downgrades)."""
    downgrades = []
    texts = [m["text"] for m in materials.values() if m and m.get("text")]
    shingles = _shingles(texts)
    self_accounts = {_account_key(u) for u in creator_urls if u}
    self_accounts |= {_account_key(m["source_url"]) for m in materials.values()
                      if m and m["type"] == "creator_article" and m.get("source_url")}

    def text(v, name, required=True):
        v = _s(v, name, required)
        if v and _has_long_quote(v, shingles):
            raise StoreError("OVERSIZED_QUOTE", f"{name} copies >= {QUOTE_WINDOW} chars of the material")
        return v

    summary = text(result.get("summary"), "summary")
    claims, ids = [], set()
    for i, c in enumerate(_l(result.get("claims"), "claims", 50)):
        cid = c.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"C\d{1,3}", cid) or cid in ids:
            raise StoreError("SCHEMA_INVALID", f"claims[{i}].id")
        if c.get("presentation") not in PRESENTATIONS:
            raise StoreError("SCHEMA_INVALID", f"{cid}.presentation")
        if c.get("category") not in CATEGORIES:
            raise StoreError("SCHEMA_INVALID", f"{cid}.category")
        if c.get("verification", "UNVERIFIED") not in CLAIM_VERIFICATION:
            raise StoreError("SCHEMA_INVALID", f"{cid}.verification")
        mids = _l(c.get("material_ids"), f"{cid}.material_ids", 10)
        if not mids:
            raise StoreError("SOURCE_REQUIRED", f"{cid} must name the material it came from")
        used = []
        for mid in mids:
            m = materials.get(mid)
            if not m:
                raise StoreError("UNKNOWN_MATERIAL", f"{cid}: {mid}")
            if m["type"] == "metadata_only":
                raise StoreError("NO_CONTENT_SOURCE", f"{cid}: {mid} is metadata_only (content not checked)")
            used.append(m)
        bases = sorted({m["basis"] for m in used})
        first_hand = any(b in FIRST_HAND_BASES for b in bases)
        ts = c.get("timestamp")
        ts = ts if isinstance(ts, str) and TIMESTAMP_RE.fullmatch(ts) else None
        if ts and not first_hand:
            downgrades.append({"item": cid, "field": "timestamp", "reason": "timestamp only kept when a person "
                               "checked the video (AI summary / article timestamps are not confirmed)"})
            ts = None
        topic = c.get("topic_key")
        if topic is not None and not (isinstance(topic, str) and TOPIC_RE.fullmatch(topic)):
            raise StoreError("SCHEMA_INVALID", f"{cid}.topic_key")
        ids.add(cid)
        claims.append({"id": cid, "text": text(c.get("text"), f"{cid}.text"), "category": c["category"],
                       "presentation": c["presentation"], "material_ids": mids, "bases": bases,
                       "content_check": "PERSON_CHECKED" if first_hand else "AI_OR_SECONDARY_ONLY",
                       "timestamp": ts, "topic_key": topic,
                       "position": _s(c.get("position"), f"{cid}.position", False, 60),
                       "verification": c.get("verification", "UNVERIFIED"),
                       "verification_required": _b(c.get("verification_required"), f"{cid}.verification_required"),
                       "conflict": _b(c.get("conflict"), f"{cid}.conflict")})

    evidence = []
    for i, e in enumerate(_l(result.get("evidence"), "evidence", 100)):
        if e.get("claim_id") not in ids:
            raise StoreError("SCHEMA_INVALID", f"evidence[{i}].claim_id")
        if e.get("kind") not in EVIDENCE_KINDS:
            raise StoreError("SCHEMA_INVALID", f"evidence[{i}].kind")
        url = e.get("source_url")
        if url is not None and not (isinstance(url, str) and re.match(r"^https?://", url) and len(url) <= 2000):
            raise StoreError("SCHEMA_INVALID", f"evidence[{i}].source_url")
        if e["kind"] in WEB_KINDS and not url:
            raise StoreError("SCHEMA_INVALID", f"evidence[{i}]: web evidence needs source_url")
        method = None
        if e["kind"] == "SOURCE_TEXT":
            claim = next(c for c in claims if c["id"] == e["claim_id"])
            own_articles = {materials[m].get("source_url") for m in claim["material_ids"]
                            if materials[m]["type"] == "creator_article"}
            if url not in own_articles:
                raise StoreError("SOURCE_TEXT_NOT_CLAIM_MATERIAL",
                                 f"evidence[{i}]: SOURCE_TEXT must point at the article {e['claim_id']} cites")
            method = e.get("method")
            if method not in SOURCE_METHODS:
                raise StoreError("SOURCE_METHOD_REQUIRED", f"evidence[{i}].method: one of {sorted(SOURCE_METHODS)}")
        evidence.append({"claim_id": e["claim_id"], "kind": e["kind"], "source_url": url, "method": method,
                         "official": _b(e.get("official"), f"evidence[{i}].official"),
                         "independent_verification": _b(e.get("independent_verification"),
                                                        f"evidence[{i}].independent_verification"),
                         "supports": _b(e.get("supports"), f"evidence[{i}].supports", None),
                         "supported_part": text(e.get("supported_part"), f"evidence[{i}].supported_part", False),
                         "unsupported_part": text(e.get("unsupported_part"), f"evidence[{i}].unsupported_part", False),
                         "note": text(e.get("note"), f"evidence[{i}].note", required=False)})
    attempted = sorted({e["claim_id"] for e in evidence if e["kind"] in WEB_KINDS})
    if len(attempted) > MAX_VERIFICATIONS:
        raise StoreError("VERIFICATION_LIMIT_EXCEEDED", f"{len(attempted)} > {MAX_VERIFICATIONS}")

    for c in claims:
        if c["verification"] != "CONFIRMED":
            continue
        own = [e for e in evidence if e["claim_id"] == c["id"]]
        good = [e for e in own if e["supports"] and e["source_url"] and e["independent_verification"]
                and _account_key(e["source_url"]) not in self_accounts
                and (e["kind"] == "PRIMARY_SOURCE" or (e["kind"] == "PUBLIC_DATA" and e["official"]))]
        if not good:
            c["verification"] = "UNVERIFIED"
            downgrades.append({"item": c["id"], "from": "CONFIRMED", "to": "UNVERIFIED",
                               "reason": "no independent primary source / official public data"})
        elif all(e["unsupported_part"] for e in good):
            c["verification"] = "UNVERIFIED"
            downgrades.append({"item": c["id"], "from": "CONFIRMED", "to": "UNVERIFIED",
                               "reason": "PARTIAL_SUPPORT: the sources back only part of the claim"})
    for c in claims:
        c["source_refs"] = [{"material_id": m, "type": materials[m]["type"], "source_url": materials[m].get("source_url")}
                            for m in c["material_ids"]]
        c["checks"] = claim_checks(c, evidence, materials)
        c["needs_review"] = needs_review(c["checks"])

    principles, pids = [], set()
    for i, p in enumerate(_l(result.get("principles"), "principles", 20)):
        pid = p.get("id")
        if not isinstance(pid, str) or not re.fullmatch(r"P\d{1,3}", pid) or pid in pids:
            raise StoreError("SCHEMA_INVALID", f"principles[{i}].id")
        if p.get("status") != "HYPOTHESIS":
            raise StoreError("INVALID_STATUS", f"{pid}.status must be HYPOTHESIS")
        derived = _l(p.get("derived_from"), f"{pid}.derived_from", 50)
        if not derived or any(d not in ids for d in derived):
            raise StoreError("SCHEMA_INVALID", f"{pid}.derived_from")
        pids.add(pid)
        principles.append({"id": pid, "statement": text(p.get("statement"), f"{pid}.statement"),
                           "derived_from": derived, "status": "HYPOTHESIS"})

    tests_in = _l(result.get("tests"), "tests", 100)
    if len(tests_in) > MAX_TESTS:
        raise StoreError("TOO_MANY_TESTS", f"{len(tests_in)} > {MAX_TESTS}")
    tests = []
    for i, t in enumerate(tests_in):
        if t.get("status") != "PROPOSED":
            raise StoreError("INVALID_STATUS", f"tests[{i}].status must be PROPOSED")
        if t.get("target") not in TARGETS:
            raise StoreError("UNKNOWN_TARGET", f"tests[{i}].target {t.get('target')!r}")
        if t.get("principle_id") not in pids:
            raise StoreError("SCHEMA_INVALID", f"tests[{i}].principle_id")
        variable = _s(t.get("variable"), f"tests[{i}].variable", max_chars=80)
        if MULTI_VARIABLE_HARD.search(variable):
            raise StoreError("MULTIPLE_VARIABLES", f"tests[{i}].variable must name ONE thing")
        kept = [text(x, f"tests[{i}].kept_constant") for x in _l(t.get("kept_constant"), "kept_constant", 20)]
        if not kept:
            raise StoreError("SCHEMA_INVALID", f"tests[{i}].kept_constant must list what stays fixed")
        tests.append({"target": t["target"], "principle_id": t["principle_id"], "variable": variable,
                      **{k: text(t.get(k), f"tests[{i}].{k}") for k in ("hypothesis", "metric", "success", "stop_rule")},
                      "kept_constant": kept, "status": "PROPOSED"})

    limitations = [text(x, "limitations") for x in _l(result.get("limitations"), "limitations", 20)]
    return {"summary": summary, "claims": claims, "evidence": evidence, "principles": principles,
            "tests": tests, "limitations": limitations,
            "verification": {"attempted": attempted, "limit": MAX_VERIFICATIONS}}, downgrades


def import_analysis(store, key, video_id, result):
    data = store.videos(key)
    v = data["videos"].get(video_id)
    if not v:
        raise StoreError("UNKNOWN_VIDEO", video_id)
    if result.get("video_id") not in (None, video_id):
        raise StoreError("VIDEO_MISMATCH", f"result is for {result.get('video_id')}")
    if "transcript" in result or "segments" in result:
        raise StoreError("FULL_TRANSCRIPT_NOT_ALLOWED", "analysis must not carry transcript text")
    materials = load_materials(store, key, video_id)
    ch = store.get_channel(key)
    clean, downgrades = validate(result, materials, creator_urls=[ch.get("canonical_url")])
    record = {"video_id": video_id, "imported_at": store.now(), **clean, "policy_downgrades": downgrades}
    write_json(store.channel_dir(key) / "analyses" / f"{video_id}.json", record)
    used = {mid for c in clean["claims"] for mid in c["material_ids"]}
    full_first_hand = any(materials[m]["coverage"] == "FULL" and materials[m]["basis"] in
                          ("HUMAN_NOTES", "AUTHORIZED_TRANSCRIPT") for m in used)
    data = store.videos(key)
    v = data["videos"][video_id]
    v["analysis"] = True
    if v["status"] not in ("ON_HOLD", "BLOCKED"):
        v["status"] = "ANALYZED" if full_first_hand else "PARTIAL"
    v["last_checked_at"] = store.now()
    store.save_videos(key, data)
    return record


def similar(a, b):
    return difflib.SequenceMatcher(None, norm_ws(a), norm_ws(b)).ratio()
