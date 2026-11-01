"""crl_net tests with a fake opener only. No socket is opened."""
import ast
import io
import json
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

from crl_net import run_sync
from crl_net.transport import ENV_KEY, Transport, TransportError

KEY = "AIzaFAKE-test-key-0000"
CID = "UC" + "B" * 22
UPL = "UU" + "B" * 22
PKG = Path(__file__).resolve().parent.parent / "ytca"


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOpener:
    def __init__(self, pages=1, http_error=None, net_error=False):
        self.reqs, self.pages, self.http_error, self.net_error = [], pages, http_error, net_error

    def __call__(self, req, timeout):
        self.reqs.append(req.full_url)
        if self.net_error:
            raise urllib.error.URLError("boom " + req.full_url)
        if self.http_error:
            body = json.dumps({"error": {"code": self.http_error, "message": "url " + req.full_url,
                                         "errors": [{"reason": "quotaExceeded", "message": req.full_url}]}})
            raise urllib.error.HTTPError(req.full_url, self.http_error, "x", {}, io.BytesIO(body.encode()))
        u = urllib.parse.urlsplit(req.full_url)
        q = urllib.parse.parse_qs(u.query)
        if u.path.endswith("/channels"):
            return FakeResp(json.dumps({"items": [{"id": CID, "contentDetails": {"relatedPlaylists": {"uploads": UPL}}}]}).encode())
        page = int((q.get("pageToken") or ["0"])[0])
        items = [{"contentDetails": {"videoId": f"NETvid{page:02d}{i:03d}", "videoPublishedAt": "2026-02-01T00:00:00Z"},
                  "snippet": {"title": f"t{page}-{i}"}} for i in range(2)]
        nxt = {"nextPageToken": str(page + 1)} if page + 1 < self.pages else {}
        return FakeResp(json.dumps({"items": items, **nxt}).encode())


class TestTransport(unittest.TestCase):
    def tr(self, **kw):
        return Transport(env={ENV_KEY: KEY}, **kw)

    def test_missing_key(self):
        with self.assertRaises(TransportError) as cm:
            Transport(env={})
        self.assertIn("MISSING_API_KEY", str(cm.exception))

    def test_only_two_endpoints_and_params(self):
        t = self.tr(opener=FakeOpener())
        with self.assertRaises(TransportError):
            t("search.list", {"part": "snippet"})
        with self.assertRaises(TransportError):
            t("channels.list", {"part": "statistics", "mine": "true"})

    def test_request_shape_and_host(self):
        op = FakeOpener()
        r = self.tr(opener=op)("channels.list", {"part": "id,contentDetails", "forHandle": "@らんさーず"})
        self.assertEqual(r["items"][0]["id"], CID)
        u = urllib.parse.urlsplit(op.reqs[0])
        self.assertEqual((u.scheme, u.hostname, u.path), ("https", "www.googleapis.com", "/youtube/v3/channels"))
        q = urllib.parse.parse_qs(u.query)
        self.assertEqual(q["forHandle"], ["@らんさーず"])
        self.assertIn("fields", q)

    def test_key_never_leaks(self):
        t = self.tr(opener=FakeOpener(http_error=403))
        self.assertNotIn(KEY, repr(t))
        r = t("channels.list", {"part": "id,contentDetails", "forHandle": "@x"})
        self.assertNotIn(KEY, json.dumps(r))
        self.assertEqual(r["error"]["errors"][0]["reason"], "quotaExceeded")
        with self.assertRaises(TransportError) as cm:
            self.tr(opener=FakeOpener(net_error=True))("channels.list", {"part": "id,contentDetails", "id": CID})
        self.assertNotIn(KEY, str(cm.exception))
        self.assertNotIn("googleapis", str(cm.exception))

    def test_no_retry(self):
        op = FakeOpener(http_error=500)
        self.tr(opener=op)("channels.list", {"part": "id,contentDetails", "id": CID})
        self.assertEqual(len(op.reqs), 1)


class TestRunSyncGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.consent = self.tmp / "api_consent.json"
        from ytca.store import Store
        Store(self.tmp / "data").add_channel("@ytca-pilot-dummy")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_(self, argv, opener):
        return run_sync.run(argv, env={ENV_KEY: KEY}, opener=opener, consent_path=self.consent,
                            data_root=self.tmp / "data")

    def test_refuses_without_consent_or_flag(self):
        op = FakeOpener()
        self.assertEqual(self.run_(["ytca-pilot-dummy", "--i-approve-network"], op)["result"], "REFUSED")
        self.consent.write_text(json.dumps({"approved_by": "owner", "accepted_youtube_terms": True,
                                            "accepted_google_privacy_policy": True}), encoding="utf-8")
        self.assertIn("CONSENT_INCOMPLETE", self.run_(["ytca-pilot-dummy", "--i-approve-network"], op)["error"])
        self.consent.write_text(json.dumps({"approved_by": "owner", "accepted_youtube_terms": True,
                                            "accepted_google_privacy_policy": True,
                                            "key_restricted_to_youtube_data_api": True}), encoding="utf-8")
        self.assertIn("NETWORK_NOT_APPROVED", self.run_(["ytca-pilot-dummy"], op)["error"])
        self.assertEqual(op.reqs, [])                                  # nothing was sent in any refused case

    def test_bounded_run_refetches_and_writes_nothing(self):
        self.consent.write_text(json.dumps({"approved_by": "owner", "accepted_youtube_terms": True,
                                            "accepted_google_privacy_policy": True,
                                            "key_restricted_to_youtube_data_api": True}), encoding="utf-8")
        files = lambda: sorted(str(p) for p in (self.tmp / "data").rglob("*"))
        before = files()
        op = FakeOpener(pages=3)
        r1 = self.run_(["ytca-pilot-dummy", "--i-approve-network", "--budget", "2"], op)
        self.assertEqual((r1["result"], r1["api"]["complete"]), ("INCOMPLETE_BUDGET", False))
        r2 = self.run_(["ytca-pilot-dummy", "--i-approve-network", "--show", "all"], op)
        self.assertEqual((r2["result"], len(r2["api"]["rows"])), ("COMPLETE", 6))
        self.assertEqual(len(op.reqs), 6)                              # 2 + (1 channel + 3 pages): starts over
        self.assertEqual(files(), before)                              # no listing / checkpoint file
        self.assertEqual(self.run_(["ytca-pilot-dummy", "--i-approve-network", "--budget", "50"], op)["result"], "ERROR")
        self.assertEqual(self.run_(["no-such-channel", "--i-approve-network"], op)["result"], "ERROR")
        self.assertEqual(len(op.reqs), 6)                              # bad budget / unknown channel: nothing sent


class TestSeparation(unittest.TestCase):
    def test_ytca_never_imports_crl_net(self):
        for f in PKG.glob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(n.startswith("crl_net") or n == "importlib" for n in names), f.name)
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "__import__":
                    self.fail(f"dynamic import in {f.name}")


if __name__ == "__main__":
    unittest.main()
