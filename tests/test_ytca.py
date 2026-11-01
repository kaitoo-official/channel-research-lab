import ast
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ytca import analysis, batch, report, synthesis
from ytca.materials import add_material
from ytca.store import Store, StoreError
from ytca.urls import UrlError, normalize_channel, normalize_video

HERE = Path(__file__).resolve().parent
PILOT = HERE / "fixtures" / "pilot"
PKG = HERE.parent / "ytca"
V = ["PILOTvid001", "PILOTvid002", "PILOTvid003", "PILOTvid004", "PILOTvid005", "PILOTvid006"]


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.clock = Clock()
        self.st = Store(self.tmp / "data", clock=self.clock)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def channel(self, url="https://www.youtube.com/@ytca-pilot-dummy"):
        ch, _ = self.st.add_channel(url)
        return ch["key"]


class TestUrls(unittest.TestCase):
    def test_target_handle_raw_and_encoded(self):
        a = normalize_channel("https://www.youtube.com/@らんさーず")
        b = normalize_channel("https://www.youtube.com/@%E3%82%89%E3%82%93%E3%81%95%E3%83%BC%E3%81%9A")
        self.assertEqual(a["key"], b["key"])
        self.assertEqual(a["handle"], "@らんさーず")
        self.assertEqual(a["kind"], "handle")

    def test_handle_tabs_and_bare(self):
        self.assertEqual(normalize_channel("youtube.com/@Some.Name/videos")["key"], "some.name")
        self.assertEqual(normalize_channel("@abc_def")["handle"], "@abc_def")

    def test_channel_id(self):
        r = normalize_channel("https://www.youtube.com/channel/UC1234567890abcdefghijAB")
        self.assertEqual(r["channel_id"], "UC1234567890abcdefghijAB")

    def test_rejects(self):
        for bad, code in [("https://www.youtube.com/c/legacy", "LEGACY_URL"),
                          ("https://www.youtube.com/watch?v=PILOTvid001", "NOT_A_CHANNEL"),
                          ("https://example.com/@x", "NOT_YOUTUBE"),
                          ("https://www.youtube.com/channel/UCshort", "BAD_CHANNEL_ID"),
                          ("https://www.youtube.com/@a", "BAD_HANDLE"),
                          ("https://www.youtube.com/@abc/videos/extra", "NOT_A_CHANNEL")]:
            with self.assertRaises(UrlError) as cm:
                normalize_channel(bad)
            self.assertEqual(cm.exception.code, code, bad)

    def test_video_forms(self):
        for u in ["PILOTvid001", "https://www.youtube.com/watch?v=PILOTvid001&t=3s", "https://youtu.be/PILOTvid001",
                  "https://www.youtube.com/shorts/PILOTvid001", "youtube.com/live/PILOTvid001"]:
            self.assertEqual(normalize_video(u), "PILOTvid001", u)
        with self.assertRaises(UrlError):
            normalize_video("https://www.youtube.com/playlist?list=PL123")


class TestStore(Base):
    def test_channel_idempotent(self):
        a, new1 = self.st.add_channel("@ytca-pilot-dummy")
        b, new2 = self.st.add_channel("https://www.youtube.com/@YTCA-pilot-dummy")
        self.assertTrue(new1)
        self.assertFalse(new2)
        self.assertEqual(a["key"], b["key"])

    def test_videos_dedupe_and_provenance(self):
        k = self.channel()
        added, known = self.st.add_videos(k, [{"url": V[0], "title": "t1", "published_at": "2026-01-01"}])
        self.assertEqual(added, [V[0]])
        added, known = self.st.add_videos(k, [{"url": "https://youtu.be/" + V[0]}])
        self.assertEqual((added, known), ([], [V[0]]))
        v = self.st.videos(k)["videos"][V[0]]
        self.assertEqual(v["fields"]["title"]["source"], "human")
        self.assertEqual(len(v["sources"]), 2)
        with self.assertRaises(StoreError):
            self.st.add_videos(k, [{"url": V[1], "published_at": "Jan 1"}])

    def test_expire_only_api_fields(self):
        k = self.channel()
        self.st.add_videos(k, [{"url": V[0], "title": "human title"}])
        data = self.st.videos(k)
        data["videos"][V[0]]["fields"]["view_hint"] = {"value": 1, "source": "api", "at": "2026-01-01T00:00:00Z"}
        self.st.save_videos(k, data)
        self.clock.t = 1_790_000_000.0   # 2026-09 -> api field older than 30 days
        self.assertEqual(self.st.expire_api_fields(k, 30), 1)
        self.assertIn("title", self.st.videos(k)["videos"][V[0]]["fields"])

    def test_coverage_unknown_until_person_confirms(self):
        k = self.channel()
        self.st.add_videos(k, [{"url": v} for v in V[:2]])
        self.assertIsNone(synthesis.coverage(self.st, k)["channel_coverage_rate"])
        with self.assertRaises(StoreError):
            self.st.confirm_video_list(k, "", "")
        self.st.confirm_video_list(k, "owner", "checked the channel page by hand")
        self.assertEqual(synthesis.coverage(self.st, k)["channel_coverage_rate"], 0.0)

    def test_hold_needs_reason_and_lock(self):
        k = self.channel()
        self.st.add_videos(k, [{"url": V[0]}])
        with self.assertRaises(StoreError):
            self.st.set_status(k, V[0], "ON_HOLD")
        with self.st.lock(k):
            with self.assertRaises(StoreError) as cm:
                with self.st.lock(k):
                    pass
            self.assertEqual(cm.exception.code, "LOCKED")


class TestMaterials(Base):
    def setUp(self):
        super().setUp()
        self.k = self.channel()
        self.st.add_videos(self.k, [{"url": v} for v in V])

    def test_rules(self):
        cases = [("notebooklm_summary", dict(text="x"), "HUMAN_REVIEW_REQUIRED"),
                 ("authorized_transcript", dict(text="x"), "NEEDS_POLICY_REVIEW"),
                 ("creator_article", dict(text="x"), "SOURCE_URL_REQUIRED"),
                 ("metadata_only", dict(text="x"), "NO_TEXT_FOR_METADATA_ONLY"),
                 ("manual_notes", dict(text=""), "TEXT_REQUIRED"),
                 ("raw_scrape", dict(text="x"), "BAD_MATERIAL_TYPE")]
        for t, kw, code in cases:
            with self.assertRaises(StoreError) as cm:
                add_material(self.st, self.k, V[0], t, **kw)
            self.assertEqual(cm.exception.code, code, t)
        with self.assertRaises(StoreError):
            add_material(self.st, self.k, "UNKNOWNvid1", "manual_notes", text="x")

    def test_dedupe(self):
        m1, new1 = add_material(self.st, self.k, V[0], "manual_notes", text="memo")
        m2, new2 = add_material(self.st, self.k, V[0], "manual_notes", text="memo")
        self.assertTrue(new1)
        self.assertFalse(new2)
        self.assertEqual(m1["material_id"], m2["material_id"])


class TestAnalysis(Base):
    def setUp(self):
        super().setUp()
        self.k = self.channel()
        self.st.add_videos(self.k, [{"url": v} for v in V])
        add_material(self.st, self.k, V[0], "manual_notes", text="人のメモ" * 30, coverage="FULL")
        add_material(self.st, self.k, V[0], "notebooklm_summary", text="AI要約の本文", human_reviewed=True)
        add_material(self.st, self.k, V[0], "metadata_only")

    def result(self, **over):
        r = {"summary": "要約", "claims": [{"id": "C1", "text": "主張", "category": "method",
                                            "presentation": "STATED", "material_ids": ["M01"]}],
             "evidence": [], "principles": [], "tests": [], "limitations": []}
        r.update(over)
        return r

    def imp(self, r):
        return analysis.import_analysis(self.st, self.k, V[0], r)

    def code(self, r):
        with self.assertRaises(StoreError) as cm:
            self.imp(r)
        return cm.exception.code

    def test_ok_and_status(self):
        rec = self.imp(self.result())
        self.assertEqual(rec["claims"][0]["content_check"], "PERSON_CHECKED")
        self.assertEqual(self.st.videos(self.k)["videos"][V[0]]["status"], "ANALYZED")

    def test_source_rules(self):
        c = self.result()["claims"][0]
        self.assertEqual(self.code(self.result(claims=[{**c, "material_ids": []}])), "SOURCE_REQUIRED")
        self.assertEqual(self.code(self.result(claims=[{**c, "material_ids": ["M09"]}])), "UNKNOWN_MATERIAL")
        self.assertEqual(self.code(self.result(claims=[{**c, "material_ids": ["M03"]}])), "NO_CONTENT_SOURCE")
        self.assertEqual(self.code({**self.result(), "transcript": "x"}), "FULL_TRANSCRIPT_NOT_ALLOWED")

    def test_ai_summary_only_is_partial_and_drops_timestamp(self):
        c = {**self.result()["claims"][0], "material_ids": ["M02"], "timestamp": "1:23"}
        rec = self.imp(self.result(claims=[c]))
        self.assertEqual(rec["claims"][0]["content_check"], "AI_OR_SECONDARY_ONLY")
        self.assertIsNone(rec["claims"][0]["timestamp"])
        self.assertEqual(self.st.videos(self.k)["videos"][V[0]]["status"], "PARTIAL")

    def test_oversized_quote(self):
        c = {**self.result()["claims"][0], "text": "人のメモ" * 16}
        self.assertEqual(self.code(self.result(claims=[c])), "OVERSIZED_QUOTE")

    def test_confirmed_needs_independent_source(self):
        c = {**self.result()["claims"][0], "category": "revenue", "verification": "CONFIRMED"}
        ev = [{"claim_id": "C1", "kind": "PRIMARY_SOURCE", "source_url": "https://www.youtube.com/@ytca-pilot-dummy",
               "supports": True, "independent_verification": True}]
        rec = self.imp(self.result(claims=[c], evidence=ev))
        self.assertEqual(rec["claims"][0]["verification"], "UNVERIFIED")   # creator's own channel page
        ev2 = [{**ev[0], "source_url": "https://www.stat.go.jp/data/x.html"}]
        rec = self.imp(self.result(claims=[c], evidence=ev2))
        self.assertEqual(rec["claims"][0]["verification"], "CONFIRMED")

    def test_principle_and_test_rules(self):
        p = [{"id": "P1", "statement": "s", "derived_from": ["C1"], "status": "CONFIRMED"}]
        self.assertEqual(self.code(self.result(principles=p)), "INVALID_STATUS")
        p[0]["status"] = "HYPOTHESIS"
        t = {"target": "kaitoo-works", "principle_id": "P1", "variable": "件名、本文", "hypothesis": "h",
             "metric": "m", "success": "s", "stop_rule": "r", "kept_constant": ["x"], "status": "PROPOSED"}
        self.assertEqual(self.code(self.result(principles=p, tests=[t])), "MULTIPLE_VARIABLES")
        t["variable"] = "件名"
        self.assertEqual(self.code(self.result(principles=p, tests=[t] * 4)), "TOO_MANY_TESTS")
        self.assertEqual(self.code(self.result(principles=p, tests=[{**t, "target": "other"}])), "UNKNOWN_TARGET")
        self.assertEqual(self.code(self.result(principles=p, tests=[{**t, "kept_constant": []}])), "SCHEMA_INVALID")
        self.imp(self.result(principles=p, tests=[t]))


class TestPilot(Base):
    """Phase 2 pilot: 6 dummy videos through manifest -> synthesis -> report."""

    def copy_pilot(self):
        dst = self.tmp / "pilot"
        shutil.copytree(PILOT, dst)
        return dst

    def test_full_pilot(self):
        man = self.copy_pilot() / "manifest.json"
        r = batch.run_manifest(self.st, man)
        self.assertIsNone(r["failed"], r)
        k = r["channel"]
        vids = self.st.videos(k)["videos"]
        self.assertEqual(len(vids), 6)
        st = {v: vids[v]["status"] for v in V}
        self.assertEqual(st, {V[0]: "ANALYZED", V[1]: "ANALYZED", V[2]: "PARTIAL", V[3]: "PARTIAL",
                              V[4]: "ANALYZED", V[5]: "ON_HOLD"})
        s = synthesis.synthesize(self.st, k)
        themes = {t["topic_key"]: t for t in s["themes"] if t["topic_key"]}
        # duplicates across 3 videos collapse into one recurring theme with the real source list
        self.assertEqual(themes["proposal-opening"]["videos"], [V[0], V[1], V[2]])
        self.assertTrue(themes["proposal-opening"]["recurring"])
        self.assertFalse(themes["template-reuse"]["recurring"])
        # divergent view ordered by publish date (Jan before Mar)
        ep = themes["early-pricing"]
        self.assertTrue(ep["divergent"])
        self.assertNotIn(ep["theme_id"], s["recurring_principles"])     # disagreement is not a principle
        self.assertIn(themes["proposal-opening"]["theme_id"], s["recurring_principles"])
        self.assertEqual([m["video_id"] for m in ep["members"]], [V[0], V[2]])
        # self-reported / creator-page confirmations are downgraded -> verification candidates remain
        self.assertTrue(all(not t["confirmed"] for t in s["themes"]))
        cats = {t["category"] for t in s["themes"] if t["theme_id"] in s["verification_candidates"]}
        self.assertEqual(cats, {"work_time", "price"})
        self.assertIsNone(s["coverage"]["channel_coverage_rate"])
        self.assertIn(V[5], [o["video_id"] for o in s["open_items"]])
        md = report.render(self.st, k)
        for h in ("網羅状況", "繰り返し語られる原則", "矛盾・見解の変化", "外部で検証すべき主張",
                  "事業で試せるテスト案", "未分析・未確認", "確認済みの動画ソース", "UNKNOWN",
                  "確認済みの根拠", "未検証の主張", "Channel Research Lab"):
            self.assertIn(h, md)

    def test_resume_after_failure(self):
        d = self.copy_pilot()
        bad = d / "analyses" / f"{V[1]}.json"
        good = bad.read_text(encoding="utf-8")
        a = json.loads(good)
        a["principles"] = [{"id": "P1", "statement": "x", "derived_from": ["C1"], "status": "CONFIRMED"}]
        bad.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        r1 = batch.run_manifest(self.st, d / "manifest.json")
        self.assertIsNotNone(r1["failed"])
        k = r1["channel"]
        self.assertIn("last_failure", self.st.journal(k))
        bad.write_text(good, encoding="utf-8")
        r2 = batch.run_manifest(self.st, d / "manifest.json")
        self.assertIsNone(r2["failed"])
        self.assertEqual(r2["skipped"], r1["done"])
        r3 = batch.run_manifest(self.st, d / "manifest.json")
        self.assertEqual(r3["done"], 0)
        v0 = self.st.videos(k)["videos"][V[0]]
        self.assertEqual(len(v0["materials"]), 1)          # no duplicate material on re-run

    def test_manifest_path_escape_blocked(self):
        d = self.copy_pilot()
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        m["items"][0]["file"] = "../../outside.txt"
        (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        with self.assertRaises(StoreError):
            batch.run_manifest(self.st, d / "manifest.json")


class TestNoNetwork(unittest.TestCase):
    FORBIDDEN = {"urllib.request", "http", "http.client", "socket", "subprocess", "requests", "ssl", "ftplib"}

    def test_package_has_no_network_or_process_imports(self):
        for f in PKG.glob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                for n in names:
                    self.assertFalse(any(n == x or n.startswith(x + ".") for x in self.FORBIDDEN), f"{f.name}: {n}")


if __name__ == "__main__":
    unittest.main()
