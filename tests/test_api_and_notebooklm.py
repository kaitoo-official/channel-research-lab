"""Mock-only tests for the in-memory API listing logic and the NotebookLM splitter. No network is used."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ytca import api, notebooklm, report, synthesis
from ytca.analysis import import_analysis
from ytca.materials import add_material
from ytca.store import Store, StoreError

CID = "UC" + "A" * 22
UPL = "UU" + "A" * 22


def vid(n):
    return f"APIvid{n:05d}"


class MockYouTube:
    """Minimal fake of channels.list / playlistItems.list. Records every call."""

    def __init__(self, videos, extra_items=(), fail=None):
        self.videos, self.extra, self.fail, self.calls = list(videos), list(extra_items), fail, []

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if self.fail == endpoint:
            return {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
        if endpoint == "channels.list":
            assert params["part"] == "id,contentDetails"
            return {"items": [{"id": CID, "contentDetails": {"relatedPlaylists": {"uploads": UPL}}}]}
        assert endpoint == "playlistItems.list" and params["playlistId"] == UPL
        assert params["part"] == "contentDetails,snippet" and params["maxResults"] == 50
        rows = [{"contentDetails": {"videoId": v, "videoPublishedAt": "2026-01-02T00:00:00Z"},
                 "snippet": {"title": f"title {v}", "description": "long text", "thumbnails": {}}}
                for v, _ in self.videos] + self.extra
        start = int(params.get("pageToken") or 0)
        page = rows[start:start + 50]
        nxt = start + 50
        return {"items": page, "pageInfo": {"totalResults": len(rows)},
                **({"nextPageToken": str(nxt)} if nxt < len(rows) else {})}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.clock = Clock(1_790_000_000.0)
        self.st = Store(self.tmp, clock=self.clock)
        self.key = self.st.add_channel("@ytca-pilot-dummy")[0]["key"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestApiListingInMemory(Base):
    """Retention model A: the listing is held in memory only; nothing API-derived is written."""

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}

    def test_full_listing_paged_in_memory_only(self):
        before = self.snapshot()
        yt = MockYouTube([(vid(i), None) for i in range(120)])
        r = api.fetch_listing(yt, self.st.get_channel(self.key), clock=self.clock)
        self.assertEqual((r["result"], len(r["items"]), r["units_used"]), ("COMPLETE", 120, 4))   # 1 + 3 pages
        self.assertEqual(set(r["items"][0]), {"video_id", "title", "published_at"})     # no description / stats
        self.assertEqual(self.snapshot(), before)                                         # nothing written
        self.assertIsNone(self.st.get_channel(self.key)["channel_id"])                    # channel ID not stored

    def test_private_and_deleted_entries_skipped(self):
        extra = [{"contentDetails": {"videoId": vid(900)}, "snippet": {"title": "Private video"}},
                 {"contentDetails": {"videoId": "bad"}, "snippet": {"title": "x"}}, "not-a-dict"]
        r = api.fetch_listing(MockYouTube([(vid(1), None)], extra), self.st.get_channel(self.key))
        self.assertEqual([i["video_id"] for i in r["items"]], [vid(1)])

    def test_budget_stop_is_not_resumable_and_refetches(self):
        before = self.snapshot()
        yt = MockYouTube([(vid(i), None) for i in range(200)])
        ch = self.st.get_channel(self.key)
        r1 = api.fetch_listing(yt, ch, budget_units=3)
        self.assertEqual((r1["result"], len(r1["items"]), r1["units_used"]), ("INCOMPLETE_BUDGET", 100, 3))
        r2 = api.fetch_listing(yt, ch)
        self.assertEqual((r2["result"], len(r2["items"])), ("COMPLETE", 200))
        self.assertEqual([c[0] for c in yt.calls].count("channels.list"), 2)       # starts over: no checkpoint
        self.assertNotIn("pageToken", yt.calls[4][1])                               # 2nd run begins at page 1
        self.assertEqual(self.snapshot(), before)

    def test_budget_hard_cap(self):
        with self.assertRaises(StoreError) as cm:
            api.fetch_listing(MockYouTube([]), self.st.get_channel(self.key), budget_units=21)
        self.assertEqual(cm.exception.code, "BAD_BUDGET")

    def test_api_error_reported(self):
        with self.assertRaises(StoreError) as cm:
            api.fetch_listing(MockYouTube([], fail="channels.list"), self.st.get_channel(self.key))
        self.assertIn("quotaExceeded", str(cm.exception))

    def test_view_shows_api_data_as_returned_and_own_counts_apart(self):
        self.st.add_videos(self.key, [{"url": vid(1), "title": "my title"}, {"url": vid(7)}])
        add_material(self.st, self.key, vid(1), "manual_notes", text="my own notes")
        r = api.fetch_listing(MockYouTube([(vid(i), None) for i in range(5)]), self.st.get_channel(self.key))
        view = api.listing_view(self.st, self.key, r, show="all", limit=2)
        self.assertEqual(view["api"]["total_results_reported_by_api"], 5)               # the API's number, not ours
        self.assertEqual(([x["video_id"] for x in view["api"]["rows"]], view["api"]["rows_truncated"]),
                         ([vid(0), vid(1)], True))
        self.assertEqual(set(view["api"]["rows"][0]), {"video_id", "title", "published_at"})   # no own data joined
        self.assertEqual(view["own_records"]["videos"], 2)
        self.assertIn("YouTube のデータではない", view["own_records"]["disclosure"])
        text = json.dumps(view, ensure_ascii=False)
        for held in ("own_status", "listed_without", "not_in_listing", "rate", "score", "rank"):
            self.assertNotIn(held, text)                                              # comparisons are on hold
        self.assertIn("youtube.com/t/terms", view["api"]["attribution"])
        self.assertNotIn("rows", api.listing_view(self.st, self.key, r)["api"])          # default: no rows
        with self.assertRaises(StoreError):
            api.listing_view(self.st, self.key, r, show="todo")

    def test_purge_removes_legacy_api_data_only(self):
        self.st.add_videos(self.key, [{"url": vid(1), "title": "my title"}])
        add_material(self.st, self.key, vid(1), "manual_notes", text="my own notes")
        d = self.st.channel_dir(self.key)
        (d / "api_listing.json").write_text('{"items": {"x": {}}}', encoding="utf-8")
        data = self.st.videos(self.key)
        data["videos"][vid(1)]["sources"].append({"type": "api", "at": "2026-01-01T00:00:00Z"})
        data["videos"][vid(1)]["api_listed"] = True
        data["videos"][vid(2)] = {"video_id": vid(2), "url": "u", "status": "UNANALYZED", "hold_reason": None,
                                  "fields": {"title": {"value": "api t", "source": "api", "at": "x"}},
                                  "sources": [{"type": "api", "at": "x"}], "materials": [], "analysis": False}
        self.st.save_videos(self.key, data)
        r = api.purge_api_data(self.st)
        self.assertEqual((r["listing_files_removed"], r["api_only_videos_removed"]), (1, [vid(2)]))
        v1 = self.st.videos(self.key)["videos"][vid(1)]
        self.assertEqual((v1["fields"]["title"]["value"], len(v1["materials"])), ("my title", 1))
        self.assertFalse(any(s["type"] == "api" for s in v1["sources"]) or "api_listed" in v1)
        self.assertFalse((d / "api_listing.json").exists())

    def test_report_has_no_api_values(self):
        self.st.add_videos(self.key, [{"url": vid(1)}])
        add_material(self.st, self.key, vid(1), "manual_notes", text="memo", coverage="FULL")
        import_analysis(self.st, self.key, vid(1), {"summary": "s", "claims": [
            {"id": "C1", "text": "c", "category": "method", "presentation": "STATED", "material_ids": ["M01"]}]})
        synthesis.synthesize(self.st, self.key)
        md = report.render(self.st, self.key)
        self.assertIn("（タイトル未入力）", md)
        self.assertNotIn("YouTube Data API", md)
        self.assertEqual(api.estimate_units(500), 11)
        self.assertEqual(api.estimate_units(0), 2)

    def test_cli_sync_refuses(self):
        from ytca.cli import main
        self.assertEqual(main(["--root", str(self.tmp), "api-sync", self.key]), 2)


class TestNotebookLMSplit(Base):
    TEXT = ("NotebookLM の前置き\n=== VIDEO: https://www.youtube.com/watch?v=PILOTvid001 ===\n要約1\n"
            "=== VIDEO: PILOTvid002 ===\n要約2\n=== VIDEO: PILOTvid009 ===\n未登録\n=== VIDEO: not-a-video ===\nx\n")

    def setUp(self):
        super().setUp()
        self.st.add_videos(self.key, [{"url": "PILOTvid001"}, {"url": "PILOTvid002"}])

    def test_split(self):
        with self.assertRaises(StoreError):
            notebooklm.import_split(self.st, self.key, self.TEXT, human_reviewed=False)
        r = notebooklm.import_split(self.st, self.key, self.TEXT, human_reviewed=True)
        self.assertEqual([a["video_id"] for a in r["added"]], ["PILOTvid001", "PILOTvid002"])
        self.assertEqual(len(r["problems"]), 3)       # preamble, unregistered, bad id
        r2 = notebooklm.import_split(self.st, self.key, self.TEXT, human_reviewed=True)
        self.assertEqual((r2["added"], len(r2["duplicates"])), ([], 2))

    def test_summary_only_claims_stay_partial(self):
        notebooklm.import_split(self.st, self.key, self.TEXT, human_reviewed=True)
        rec = import_analysis(self.st, self.key, "PILOTvid001", {
            "summary": "s", "claims": [{"id": "C1", "text": "c", "category": "method", "presentation": "STATED",
                                        "material_ids": ["M01"], "timestamp": "0:10"}]})
        self.assertEqual(rec["claims"][0]["content_check"], "AI_OR_SECONDARY_ONLY")
        self.assertEqual(self.st.videos(self.key)["videos"]["PILOTvid001"]["status"], "PARTIAL")


if __name__ == "__main__":
    unittest.main()
