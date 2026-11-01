"""Phase 6.4: article-only items, distinct-source recurrence, comparison method, partial fact support."""
import shutil
import tempfile
import unittest
from pathlib import Path

from ytca import analysis, report, synthesis
from ytca.materials import add_material
from ytca.store import Store, StoreError

A1 = "https://note.com/example_corp/n/n111111111111"
A2 = "https://note.com/example_corp/n/n222222222222"
VID = "VIDEOwithA3"
A3 = "https://note.com/example_corp/n/n333333333333"


class Clock:
    def __call__(self):
        return 1_790_000_000.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.st = Store(self.tmp / "data", clock=Clock())
        ch, _ = self.st.add_channel("https://www.youtube.com/@ytca-cross-dummy")
        self.k = ch["key"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def art(self, url, date, text):
        item, _ = self.st.add_article_item(self.k, url, title=f"記事{url[-1]}", published_at=date)
        add_material(self.st, self.k, item, "creator_article", text=text, source_url=url, source_title=f"記事{url[-1]}",
                     source_published_at=date)
        return item

    def imp(self, item, url, claims, evidence=None, principles=(), tests=()):
        ev = evidence if evidence is not None else [
            {"claim_id": c["id"], "kind": "SOURCE_TEXT", "source_url": url, "supports": True,
             "method": "AI_MEDIATED_FETCH"} for c in claims]
        r = {"summary": "要約", "claims": claims, "evidence": ev, "principles": list(principles),
             "tests": list(tests), "limitations": []}
        return analysis.import_analysis(self.st, self.k, item, r)


def claim(cid, topic, cat="method", **kw):
    return {"id": cid, "text": f"発信者は{topic}について{cid}と述べている", "category": cat,
            "presentation": "STATED", "material_ids": ["M01"], "topic_key": topic, **kw}


class TestArticleItems(Base):
    def test_article_item_is_not_a_video(self):
        item = self.art(A1, "2026-05-26", "要約1")
        self.assertEqual(item, "art-n111111111111")
        v = self.st.videos(self.k)["videos"][item]
        self.assertEqual((v["kind"], v["url"]), ("article", A1))
        cov = synthesis.coverage(self.st, self.k)
        self.assertEqual((cov["known_videos"], cov["article_items"]), (0, 1))

    def test_article_item_takes_only_its_own_article(self):
        item, _ = self.st.add_article_item(self.k, A1)
        for kw, code in [(dict(mtype="manual_notes", text="memo"), "ARTICLE_ITEM_MATERIAL"),
                         (dict(mtype="creator_article", text="x", source_url=A2), "ARTICLE_ITEM_MATERIAL"),
                         (dict(mtype="creator_article", text="x", source_url=A1,
                               linked_video_url="https://youtu.be/VIDEOwithA3"), "VIDEO_LINK_MISMATCH")]:
            mtype = kw.pop("mtype")
            with self.assertRaises(StoreError) as cm:
                add_material(self.st, self.k, item, mtype, **kw)
            self.assertEqual(cm.exception.code, code)

    def test_same_slug_other_url_is_refused(self):
        self.st.add_article_item(self.k, A1)
        with self.assertRaises(StoreError) as cm:
            self.st.add_article_item(self.k, "https://other.example/n111111111111")
        self.assertEqual(cm.exception.code, "ARTICLE_ID_CLASH")


class TestRecurrence(Base):
    def test_two_articles_recur_one_article_twice_does_not(self):
        i1 = self.art(A1, "2026-05-26", "要約1")
        i2 = self.art(A2, "2026-06-03", "要約2")
        self.imp(i1, A1, [claim("C1", "narrow-niche"), claim("C2", "only-here"), claim("C3", "only-here")])
        self.imp(i2, A2, [claim("C1", "narrow-niche")])
        s = synthesis.synthesize(self.st, self.k)
        themes = {t["topic_key"]: t for t in s["themes"]}
        self.assertTrue(themes["narrow-niche"]["recurring"])
        self.assertEqual(themes["narrow-niche"]["origins"], [A1, A2])
        self.assertFalse(themes["only-here"]["recurring"])          # repeated inside one article only
        md = report.render(self.st, self.k)
        self.assertIn("別々の出典 2 件", md)
        self.assertIn(A1, md)
        self.assertIn(A2, md)
        # timeline by article date, labelled as such
        tl = md.split("## 時系列", 1)[1].split("\n## ", 1)[0]
        self.assertLess(tl.index("2026-05-26"), tl.index("2026-06-03"))

    def test_article_that_is_the_text_of_a_video_is_one_source(self):
        self.st.add_videos(self.k, [{"url": VID}])
        add_material(self.st, self.k, VID, "creator_article", text="記事3の要約", source_url=A3)
        i1 = self.art(A1, "2026-05-26", "要約1")
        self.imp(VID, A3, [claim("C1", "same-talk")])
        self.imp(i1, A1, [claim("C1", "same-talk")])
        t = next(t for t in synthesis.synthesize(self.st, self.k)["themes"] if t["topic_key"] == "same-talk")
        self.assertTrue(t["recurring"])                               # different talks so far
        self.st.link_same_content(self.k, i1, VID, "TITLE_MATCH", note="titles match")
        t = next(t for t in synthesis.synthesize(self.st, self.k)["themes"] if t["topic_key"] == "same-talk")
        self.assertEqual(t["origins"], [f"item:{VID}"])
        self.assertFalse(t["recurring"])                              # same talk twice is one source
        with self.assertRaises(StoreError):
            self.st.link_same_content(self.k, VID, VID, "TITLE_MATCH")

    def test_two_wordings_inside_one_article_are_not_divergent(self):
        i1 = self.art(A1, "2026-05-26", "要約1")
        self.imp(i1, A1, [claim("C1", "pricing", position="絞る"), claim("C2", "pricing", position="深く届ける")])
        t = next(t for t in synthesis.synthesize(self.st, self.k)["themes"] if t["topic_key"] == "pricing")
        self.assertFalse(t["divergent"])

    def test_divergent_members_show_article_date_basis(self):
        i1 = self.art(A1, "2026-05-26", "要約1")
        i2 = self.art(A2, "2026-06-03", "要約2")
        self.imp(i1, A1, [claim("C1", "pricing", position="安く")])
        self.imp(i2, A2, [claim("C1", "pricing", position="高く")])
        s = synthesis.synthesize(self.st, self.k)
        t = next(t for t in s["themes"] if t["topic_key"] == "pricing")
        self.assertTrue(t["divergent"])
        self.assertEqual([m["date_basis"] for m in t["members"]], ["article", "article"])
        self.assertIn("（記事の公開日）", report.render(self.st, self.k))


class TestMethodAndPartialSupport(Base):
    def setUp(self):
        super().setUp()
        self.item = self.art(A1, "2026-05-26", "要約1")

    def test_method_is_required_and_shown(self):
        c = [claim("C1", "any-topic")]
        bad = [{"claim_id": "C1", "kind": "SOURCE_TEXT", "source_url": A1, "supports": True}]
        with self.assertRaises(StoreError) as cm:
            self.imp(self.item, A1, c, evidence=bad)
        self.assertEqual(cm.exception.code, "SOURCE_METHOD_REQUIRED")
        rec = self.imp(self.item, A1, c)
        self.assertEqual(rec["claims"][0]["checks"]["source_method"], "AI_MEDIATED_FETCH")
        synthesis.synthesize(self.st, self.k)
        self.assertIn("記事照合済（AI経由）", report.render(self.st, self.k))

    def test_partial_support_never_confirms(self):
        c = [claim("C1", "price", cat="price", verification="CONFIRMED")]
        ev = [{"claim_id": "C1", "kind": "PRIMARY_SOURCE", "source_url": "https://www.example.go.jp/p.html",
               "supports": True, "independent_verification": True, "official": True,
               "supported_part": "主力プランの価格", "unsupported_part": "小規模店が高く感じるという見解"}]
        rec = self.imp(self.item, A1, c, evidence=ev)
        self.assertEqual(rec["claims"][0]["verification"], "UNVERIFIED")
        self.assertIn("PARTIAL_SUPPORT", rec["policy_downgrades"][0]["reason"])
        s = synthesis.synthesize(self.st, self.k)
        r = s["verification_results"][0]
        self.assertEqual((r["claim_id"], r["final"]), ("C1", "UNVERIFIED"))
        md = report.render(self.st, self.k)
        for frag in ("## 外部検証の結果", "裏付けられた範囲：主力プランの価格",
                     "裏付けられなかった部分：小規模店が高く感じるという見解", "最終判定：UNVERIFIED", "PARTIAL_SUPPORT"):
            self.assertIn(frag, md)

    def test_full_support_confirms(self):
        c = [claim("C1", "price", cat="price", verification="CONFIRMED")]
        ev = [{"claim_id": "C1", "kind": "PRIMARY_SOURCE", "source_url": "https://www.example.go.jp/p.html",
               "supports": True, "independent_verification": True, "official": True, "supported_part": "全体"}]
        self.assertEqual(self.imp(self.item, A1, c, evidence=ev)["claims"][0]["verification"], "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
