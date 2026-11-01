"""Phase 4.5 final mock tests for retention model A (owner-approved 2026-10-27). No socket is ever opened:
urllib.request.urlopen and socket.socket.connect are patched to fail for every test in this module.

1. 300-500 video mock channels are listed completely in one run
2. a run stopped by the budget is re-fetched safely (from the start, nothing stored in between)
3. never more than 20 units
4. nothing API-derived is persisted (whole temp tree is scanned for every mock ID / title / token)
5. the owner's own notes, materials and analyses survive (listed, unlisted, purge)
6. bad responses / exceptions mid-run leave nothing behind
7. the API key never appears in any output
8. zero real network calls
"""
import io
import json
import shutil
import socket
import tempfile
import unittest
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from crl_net import run_sync
from crl_net.transport import ENV_KEY
from ytca import api, report, synthesis
from ytca.analysis import import_analysis
from ytca.materials import add_material
from ytca.store import Store

KEY = "AIzaFAKE-retention-key-9999"
CID = "UC" + "R" * 22
UPL = "UU" + "R" * 22
CONSENT = {"approved_by": "owner", "accepted_youtube_terms": True, "accepted_google_privacy_policy": True,
           "key_restricted_to_youtube_data_api": True}


def vid(n):
    return f"RETvid{n:05d}"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeChannelOpener:
    """Stands in for urllib's opener. n uploads, 50 per page; optional faults on a given page."""

    def __init__(self, n, bad_page=None, fault="garbage"):
        self.n, self.bad_page, self.fault, self.reqs = n, bad_page, fault, []

    def __call__(self, req, timeout):
        self.reqs.append(req.full_url)
        u = urllib.parse.urlsplit(req.full_url)
        q = urllib.parse.parse_qs(u.query)
        if u.path.endswith("/channels"):
            return Resp(json.dumps({"items": [{"id": CID, "contentDetails": {"relatedPlaylists": {"uploads": UPL}}}]}).encode())
        start = int((q.get("pageToken") or ["TOKEN-0"])[0].split("-")[1])
        page = start // 50
        if page == self.bad_page:
            if self.fault == "garbage":
                return Resp(b"<html>not json")
            if self.fault == "not_object":
                return Resp(b"[1, 2, 3]")
            if self.fault == "bad_items":
                return Resp(json.dumps({"items": "oops"}).encode())
            if self.fault == "bad_token":
                return Resp(json.dumps({"items": [], "nextPageToken": 12}).encode())
            raise OSError("connection reset " + req.full_url)
        rows = [{"contentDetails": {"videoId": vid(i), "videoPublishedAt": "2026-03-04T00:00:00Z"},
                 "snippet": {"title": f"TITLE-{vid(i)}"}} for i in range(start, min(start + 50, self.n))]
        nxt = {"nextPageToken": f"TOKEN-{start + 50}"} if start + 50 < self.n else {}
        return Resp(json.dumps({"items": rows, "pageInfo": {"totalResults": self.n}, **nxt}).encode())


def _no_network(*a, **k):
    raise AssertionError("real network access attempted")


class Base(unittest.TestCase):
    def setUp(self):
        self._patches = [mock.patch.object(urllib.request, "urlopen", _no_network),
                         mock.patch.object(socket.socket, "connect", _no_network)]
        for p in self._patches:
            p.start()
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.consent = self.tmp / "api_consent.json"
        self.consent.write_text(json.dumps(CONSENT), encoding="utf-8")
        self.st = Store(self.data)
        self.key = self.st.add_channel("@retention-mock")[0]["key"]

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_(self, opener, *extra):
        return run_sync.run([self.key, "--i-approve-network", *extra], env={ENV_KEY: KEY}, opener=opener,
                            consent_path=self.consent, data_root=self.data)

    def snapshot(self):
        return {p.relative_to(self.tmp).as_posix(): p.read_bytes() for p in self.tmp.rglob("*") if p.is_file()}

    def assert_no_api_data_on_disk(self, n):
        blob = b"".join(self.snapshot().values()).decode("utf-8", "replace")
        for needle in (CID, UPL, "TOKEN-", "TITLE-", "2026-03-04", "api_listing"):
            self.assertNotIn(needle, blob, needle)
        own = set(self.st.videos(self.key)["videos"])
        for i in range(n):
            if vid(i) not in own:                    # only IDs the owner registered by hand may appear
                self.assertNotIn(vid(i), blob, vid(i))


class TestRetentionModelA(Base):
    def test_1_and_3_full_listing_300_to_500_within_budget(self):
        for n, units in ((300, 7), (437, 10), (500, 11)):
            op = FakeChannelOpener(n)
            r = self.run_(op, "--show", "all", "--limit", "1000")
            self.assertEqual((r["result"], len(r["api"]["rows"]), r["run"]["units_used"]), ("COMPLETE", n, units))
            self.assertEqual(r["api"]["total_results_reported_by_api"], n)
            self.assertEqual(len(op.reqs), units)
            self.assertLessEqual(len(op.reqs), api.MAX_BUDGET)
            self.assert_no_api_data_on_disk(n)

    def test_2_budget_stop_then_safe_refetch(self):
        before = self.snapshot()
        op = FakeChannelOpener(500)
        r1 = self.run_(op, "--budget", "5", "--show", "all", "--limit", "1000")
        self.assertEqual((r1["result"], len(r1["api"]["rows"]), r1["api"]["complete"]),
                         ("INCOMPLETE_BUDGET", 200, False))
        self.assertIn("全件ではない", r1["api"]["note"])
        self.assertEqual(self.snapshot(), before)                 # no checkpoint / token kept
        r2 = self.run_(op, "--show", "all", "--limit", "1000")
        self.assertEqual((r2["result"], len(r2["api"]["rows"])), ("COMPLETE", 500))
        self.assertNotIn("pageToken", op.reqs[6])                 # 2nd run restarts at page 1 (op.reqs[5] = channels)
        self.assertEqual(self.snapshot(), before)

    def test_3_never_more_than_20_units(self):
        op = FakeChannelOpener(1200)                              # larger than one run can hold
        r = self.run_(op, "--show", "all", "--limit", "2000")
        self.assertEqual((r["result"], r["run"]["units_used"], len(op.reqs)), ("INCOMPLETE_BUDGET", 20, 20))
        self.assertEqual(len(r["api"]["rows"]), 950)
        op2 = FakeChannelOpener(10)
        self.assertEqual(self.run_(op2, "--budget", "21")["result"], "ERROR")
        self.assertEqual(op2.reqs, [])

    def test_4_nothing_persisted_even_when_rows_are_shown(self):
        r = self.run_(FakeChannelOpener(320), "--show", "all", "--limit", "500")
        self.assertEqual(len(r["api"]["rows"]), 320)              # displayed in this run ...
        self.assert_no_api_data_on_disk(320)                      # ... never written
        self.assertEqual(set(self.st.videos(self.key)["videos"]), set())
        self.assertIsNone(self.st.get_channel(self.key)["channel_id"])

    def test_5_own_notes_survive(self):
        self.st.add_videos(self.key, [{"url": vid(3), "title": "自分で付けた題", "published_at": "2026-03-04"},
                                      {"url": "OWNvid00001"}])
        add_material(self.st, self.key, vid(3), "manual_notes", text="自分のメモ", coverage="FULL")
        add_material(self.st, self.key, "OWNvid00001", "manual_notes", text="一覧に無い動画のメモ")
        import_analysis(self.st, self.key, vid(3), {"summary": "s", "claims": [
            {"id": "C1", "text": "自分の分析", "category": "method", "presentation": "STATED", "material_ids": ["M01"]}]})
        own_before = {p: b for p, b in self.snapshot().items() if "/materials/" in p or "/analyses/" in p}
        r = self.run_(FakeChannelOpener(300))
        self.assertEqual({k: v for k, v in r["own_records"].items() if k != "disclosure"},
                         {"videos": 2, "analyzed": 1, "with_material_or_analysis": 2})
        api.purge_api_data(self.st)
        own_after = {p: b for p, b in self.snapshot().items() if "/materials/" in p or "/analyses/" in p}
        self.assertEqual(own_after, own_before)
        vids = self.st.videos(self.key)["videos"]
        self.assertEqual(vids[vid(3)]["fields"]["title"]["value"], "自分で付けた題")
        self.assertIn("OWNvid00001", vids)                        # not in the listing -> still kept
        synthesis.synthesize(self.st, self.key)
        self.assertIn("自分の分析", report.render(self.st, self.key))

    def test_6_bad_responses_and_crashes_leave_nothing(self):
        before = self.snapshot()
        for fault in ("garbage", "not_object", "bad_items", "bad_token", "crash"):
            op = FakeChannelOpener(400, bad_page=3, fault=fault)
            r = self.run_(op)                                     # reported as ERROR, never an uncaught exception
            self.assertEqual(r["result"], "ERROR", fault)
            self.assertEqual(len(op.reqs), 5, fault)              # stopped at the bad page, no retry
            self.assertEqual(self.snapshot(), before, fault)
            self.assertNotIn("lock.json", "".join(self.snapshot()))

    def test_7_key_never_in_output(self):
        outputs = []
        for op in (FakeChannelOpener(120), FakeChannelOpener(120, bad_page=1, fault="crash"),
                   FakeChannelOpener(120, bad_page=0, fault="garbage")):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                r = self.run_(op, "--show", "all")
                print(json.dumps(r, ensure_ascii=False))
            outputs += [out.getvalue(), err.getvalue(), repr(r)]
        blob = "\n".join(outputs + list(b.decode("utf-8", "replace") for b in self.snapshot().values()))
        self.assertNotIn(KEY, blob)
        self.assertNotIn("googleapis.com", blob)                  # request URLs (which carry the key) never shown

    def test_8_zero_real_network(self):
        hits = []
        with mock.patch.object(urllib.request, "urlopen", lambda *a, **k: hits.append(1) or _no_network()):
            r = run_sync.run([self.key, "--i-approve-network"], env={ENV_KEY: KEY}, opener=None,
                             consent_path=self.consent, data_root=self.data)
        self.assertEqual((r["result"], len(hits)), ("ERROR", 1))  # the default opener was stopped by the guard
        self.assertNotIn(KEY, json.dumps(r))


if __name__ == "__main__":
    unittest.main()
