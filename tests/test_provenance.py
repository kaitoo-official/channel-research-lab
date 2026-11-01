"""Phase 6.3: source tracking for creator articles, the three check states, and reference integrity."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ytca import analysis, report, synthesis
from ytca.materials import add_material
from ytca.store import Store, StoreError

VID = "ARTICLEvid1"
ART = "https://note.com/example_corp/n/n0123456789ab"


class Clock:
    def __call__(self):
        return 1_790_000_000.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.st = Store(self.tmp / "data", clock=Clock())
        ch, _ = self.st.add_channel("https://www.youtube.com/@らんさーず")
        self.k = ch["key"]
        self.st.add_videos(self.k, [{"url": VID}], note="URL from the creator article")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def article(self, **over):
        kw = dict(text="記事の要約メモ。発信者は A と述べている。", source_url=ART, source_title="記事タイトル",
                  source_published_at="2026-05-26", linked_video_url=f"https://www.youtube.com/watch?v={VID}",
                  linked_channel_url="https://www.youtube.com/@らんさーズ")
        kw.update(over)
        return add_material(self.st, self.k, VID, "creator_article", **kw)

    def claim(self, cid="C1", **over):
        c = {"id": cid, "text": f"発信者は{cid}と述べている", "category": "method", "presentation": "STATED",
             "material_ids": ["M01"]}
        c.update(over)
        return c

    def result(self, claims=None, evidence=(), principles=(), tests=()):
        return {"summary": "要約", "claims": claims or [self.claim()], "evidence": list(evidence),
                "principles": list(principles), "tests": list(tests), "limitations": []}

    def imp(self, r):
        return analysis.import_analysis(self.st, self.k, VID, r)


class TestArticleMaterial(Base):
    def test_metadata_and_links_are_recorded(self):
        m, new = self.article()
        self.assertTrue(new)
        self.assertEqual(m["type"], "creator_article")
        self.assertEqual(m["source_title"], "記事タイトル")
        self.assertEqual(m["source_published_at"], "2026-05-26")
        self.assertEqual(m["linked_video"], {"video_id": VID, "url": f"https://www.youtube.com/watch?v={VID}",
                                             "basis": "ARTICLE_LINK"})
        self.assertFalse(m["video_directly_analyzed"])

    def test_channel_never_verified_by_looks(self):
        m, _ = self.article()                                    # ズ (katakana) vs ず (registered)
        self.assertEqual(m["linked_channel"]["status"], "CHANNEL_ID_UNVERIFIED")
        self.assertFalse(m["linked_channel"]["handle_matches_registered"])
        m2, _ = self.article(text="別の要約", linked_channel_url="https://www.youtube.com/@らんさーず")
        self.assertTrue(m2["linked_channel"]["handle_matches_registered"])
        self.assertEqual(m2["linked_channel"]["status"], "CHANNEL_ID_UNVERIFIED")   # same handle text is not proof

    def test_linked_video_must_be_this_video(self):
        with self.assertRaises(StoreError) as cm:
            self.article(linked_video_url="https://www.youtube.com/watch?v=OTHERvid001")
        self.assertEqual(cm.exception.code, "VIDEO_LINK_MISMATCH")

    def test_bad_date_and_fields_on_other_types(self):
        with self.assertRaises(StoreError) as cm:
            self.article(source_published_at="5月26日")
        self.assertEqual(cm.exception.code, "BAD_DATE")
        with self.assertRaises(StoreError) as cm:
            add_material(self.st, self.k, VID, "manual_notes", text="memo", source_title="x")
        self.assertEqual(cm.exception.code, "ARTICLE_FIELDS_ONLY")

    def test_rerun_fills_missing_metadata_without_overwriting(self):
        m1, _ = add_material(self.st, self.k, VID, "creator_article", text="要約", source_url=ART)
        self.assertIsNone(m1["source_title"])
        m2, new = add_material(self.st, self.k, VID, "creator_article", text="要約", source_url=ART,
                               source_title="記事タイトル", source_published_at="2026-05-26")
        self.assertFalse(new)
        self.assertEqual((m2["material_id"], m2["source_title"]), ("M01", "記事タイトル"))
        self.assertEqual(m2["text"], "要約")
        with self.assertRaises(StoreError) as cm:
            add_material(self.st, self.k, VID, "creator_article", text="要約", source_url=ART, source_title="別名")
        self.assertEqual(cm.exception.code, "METADATA_CONFLICT")


class TestCheckStates(Base):
    def setUp(self):
        super().setUp()
        self.article()

    def test_article_only_claim_is_unchecked_and_needs_review(self):
        c = self.imp(self.result())["claims"][0]
        self.assertEqual(c["checks"], {"source": "SOURCE_UNCHECKED", "source_method": None,
                                       "video": "VIDEO_UNVERIFIED", "fact": "FACT_UNVERIFIED"})
        self.assertEqual(c["source_refs"], [{"material_id": "M01", "type": "creator_article", "source_url": ART}])
        self.assertTrue(c["needs_review"])
        self.assertEqual(c["verification"], "UNVERIFIED")

    def test_source_text_marks_source_verified_but_never_fact(self):
        ev = [{"claim_id": "C1", "kind": "SOURCE_TEXT", "source_url": ART, "supports": True, "method": "AI_MEDIATED_FETCH"}]
        c = self.imp(self.result(claims=[self.claim(verification="CONFIRMED")], evidence=ev))["claims"][0]
        self.assertEqual(c["checks"]["source"], "SOURCE_VERIFIED")
        self.assertEqual(c["checks"]["video"], "VIDEO_UNVERIFIED")
        self.assertEqual(c["checks"]["fact"], "FACT_UNVERIFIED")
        self.assertEqual(c["verification"], "UNVERIFIED")              # the article never confirms the claim
        self.assertFalse(c["needs_review"])

    def test_source_text_contradiction(self):
        ev = [{"claim_id": "C1", "kind": "SOURCE_TEXT", "source_url": ART, "supports": False, "method": "HUMAN_READ"}]
        c = self.imp(self.result(evidence=ev))["claims"][0]
        self.assertEqual(c["checks"]["source"], "SOURCE_CONTRADICTED")
        self.assertTrue(c["needs_review"])

    def test_source_text_must_point_at_the_claims_article(self):
        ev = [{"claim_id": "C1", "kind": "SOURCE_TEXT", "source_url": "https://example.com/other", "supports": True, "method": "DIRECT_TEXT"}]
        with self.assertRaises(StoreError) as cm:
            self.imp(self.result(evidence=ev))
        self.assertEqual(cm.exception.code, "SOURCE_TEXT_NOT_CLAIM_MATERIAL")

    def test_source_text_does_not_use_web_verification_budget(self):
        claims = [self.claim(f"C{i}") for i in range(1, 8)]
        ev = [{"claim_id": c["id"], "kind": "SOURCE_TEXT", "source_url": ART, "supports": True, "method": "AI_MEDIATED_FETCH"} for c in claims]
        rec = self.imp(self.result(claims=claims, evidence=ev))
        self.assertEqual(rec["verification"]["attempted"], [])

    def test_independent_source_gives_fact_verified(self):
        c = self.claim(category="price", verification="CONFIRMED")
        ev = [{"claim_id": "C1", "kind": "PRIMARY_SOURCE", "source_url": "https://www.stat.go.jp/x.html",
               "supports": True, "independent_verification": True, "official": True}]
        out = self.imp(self.result(claims=[c], evidence=ev))["claims"][0]
        self.assertEqual(out["checks"]["fact"], "FACT_VERIFIED")
        self.assertEqual(out["checks"]["video"], "VIDEO_UNVERIFIED")      # a fact check is not a video check

    def test_person_checked_video(self):
        add_material(self.st, self.k, VID, "manual_notes", text="自分のメモ" * 10, coverage="FULL")
        c = self.imp(self.result(claims=[self.claim(material_ids=["M02"])]))["claims"][0]
        self.assertEqual(c["checks"], {"source": "NOT_APPLICABLE", "source_method": None,
                                       "video": "VIDEO_VERIFIED", "fact": "FACT_UNVERIFIED"})
        self.assertFalse(c["needs_review"])


class TestSynthesisAndReport(Base):
    def setUp(self):
        super().setUp()
        self.article()
        claims = [self.claim("C1", topic_key="dev-speed"),
                  self.claim("C2", topic_key="dev-speed", category="customer_outcome", verification_required=True),
                  self.claim("C3", category="opinion")]
        principles = [{"id": "P1", "statement": "仮説の文", "derived_from": ["C1", "C3"], "status": "HYPOTHESIS"}]
        tests = [{"target": "kaitoo-works", "principle_id": "P1", "variable": "紹介文の対象業種",
                  "hypothesis": "h", "metric": "m", "success": "s", "stop_rule": "r", "kept_constant": ["価格"],
                  "status": "PROPOSED"}]
        ev = [{"claim_id": "C3", "kind": "SOURCE_TEXT", "source_url": ART, "supports": True, "method": "AI_MEDIATED_FETCH"}]
        self.imp(self.result(claims=claims, evidence=ev, principles=principles, tests=tests))
        self.s = synthesis.synthesize(self.st, self.k)
        self.md = report.render(self.st, self.k)

    def test_mixed_theme_with_numbers_is_a_verification_candidate(self):
        t = next(t for t in self.s["themes"] if t["topic_key"] == "dev-speed")
        self.assertEqual(t["category"], "method")                      # first member decides the label
        self.assertIn(t["theme_id"], self.s["verification_candidates"])

    def test_tests_resolve_their_principle(self):
        t = self.s["tests"][0]
        self.assertEqual(t["principle"]["ref"], f"{VID}:P1")
        self.assertEqual(t["principle"]["statement"], "仮説の文")
        self.assertEqual(t["principle"]["derived_from"], ["C1", "C3"])
        self.assertEqual(self.s["integrity_errors"], [])

    def test_broken_references_are_reported(self):
        p = self.st.channel_dir(self.k) / "analyses" / f"{VID}.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["tests"][0]["principle_id"] = "P9"
        rec["principles"][0]["derived_from"].append("C99")
        rec["claims"][0]["material_ids"] = ["M07"]
        p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        errs = synthesis.synthesize(self.st, self.k)["integrity_errors"]
        self.assertEqual(sorted(e["code"] for e in errs),
                         ["CLAIM_MATERIAL_MISSING", "PRINCIPLE_CLAIM_MISSING", "TEST_PRINCIPLE_MISSING"])
        self.assertIn("参照の不整合", report.render(self.st, self.k))

    def test_report_traces_every_claim_to_the_article(self):
        for s in ("記事タイトル", ART, "2026-05-26", "creator_article", "CHANNEL_ID_UNVERIFIED",
                  "動画は直接分析していない", "[S1]", "C1", "C2", "C3", f"{VID}:P1", "仮説の文"):
            self.assertIn(s, self.md)
        self.assertNotIn("一部確認", self.md)
        self.assertNotIn("AI要約・二次情報のみ", self.md)               # an article is not an AI summary

    def test_article_only_video_is_not_listed_as_checked_source(self):
        sec = self.md.split("## 確認済みの動画ソース", 1)[1]
        self.assertIn("なし", sec.split("\n## ", 1)[0])
        self.assertIn("要確認", self.md)
        self.assertIn("記事照合済", self.md)                            # C3 has SOURCE_TEXT

    def test_article_without_channel_link_is_channel_unverified(self):
        add_material(self.st, self.k, VID, "creator_article", text="別記事の要約", source_url=ART + "x")
        synthesis.synthesize(self.st, self.k)
        md = report.render(self.st, self.k)
        self.assertIn("記事内にチャンネルへのリンクなし　CHANNEL_ID_UNVERIFIED", md)

    def test_old_records_without_checks_still_render(self):
        p = self.st.channel_dir(self.k) / "analyses" / f"{VID}.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        for c in rec["claims"]:
            c.pop("checks"), c.pop("needs_review")
        p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        synthesis.synthesize(self.st, self.k)
        self.assertIn("要確認", report.render(self.st, self.k))


if __name__ == "__main__":
    unittest.main()
