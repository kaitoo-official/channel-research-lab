"""One bounded, owner-started listing run (display only):
    python -m crl_net.run_sync <channel key> --i-approve-network [--budget N] [--show none|all] [--limit N]

Refuses to run unless ALL of these hold:
  1. config/api_consent.json exists, written by the owner (accepts YouTube ToS + Google Privacy Policy, names the
     Google Cloud project, confirms the key is restricted to YouTube Data API v3)
  2. --i-approve-network is passed on this command line
  3. the environment variable CRL_YOUTUBE_API_KEY is set
  4. the channel is registered locally (checked before anything is sent)
Retention model A: the listing lives in memory for this run and is printed, never written (no file, no log, no
checkpoint). A run stopped by the budget is incomplete; run it again later (it starts over).
Budget: at most 20 units per run (ytca.api.MAX_BUDGET). No retry, no loop, no scheduler.
"""
import argparse
import json
import sys
from pathlib import Path

from ytca import api
from ytca.store import Store, StoreError

from .transport import Transport, TransportError

ROOT = Path(__file__).resolve().parent.parent
CONSENT = ROOT / "config" / "api_consent.json"
REQUIRED_CONSENT = ("accepted_youtube_terms", "accepted_google_privacy_policy", "key_restricted_to_youtube_data_api")


def check_consent(path=CONSENT):
    if not path.exists():
        return "NO_CONSENT_FILE: the owner has not approved API use (config/api_consent.json)"
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "BAD_CONSENT_FILE"
    missing = [k for k in REQUIRED_CONSENT if c.get(k) is not True]
    if missing or not str(c.get("approved_by") or "").strip():
        return f"CONSENT_INCOMPLETE: {missing or ['approved_by']}"
    return None


def run(argv=None, env=None, opener=None, consent_path=CONSENT, data_root=ROOT / "data", clock=None):
    ap = argparse.ArgumentParser(prog="crl_net.run_sync")
    ap.add_argument("channel")
    ap.add_argument("--i-approve-network", action="store_true")
    ap.add_argument("--budget", type=int, default=api.MAX_BUDGET)
    ap.add_argument("--show", choices=("none", "all"), default="none",
                    help="none: no rows (default) / all: the API rows as returned, up to --limit")
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args(argv)
    problem = check_consent(consent_path)
    if not problem and not a.i_approve_network:
        problem = "NETWORK_NOT_APPROVED_ON_COMMAND_LINE: pass --i-approve-network"
    if problem:
        return {"result": "REFUSED", "error": problem}
    store = Store(data_root, clock=clock)
    try:
        api.purge_api_data(store)                      # local cleanup of anything an older version stored
        channel = store.get_channel(a.channel)         # unknown channel -> error before any request
        api.Budget(a.budget)                           # bad budget -> error before any request
        transport = Transport(env=env, opener=opener)
        listing = api.fetch_listing(transport, channel, budget_units=a.budget, clock=clock)
        view = api.listing_view(store, a.channel, listing, show=a.show, limit=a.limit)
        result = listing["result"]
        del listing                                    # nothing of the response outlives this function
        return {"result": result, **view}
    except (TransportError, StoreError) as e:
        return {"result": "ERROR", "error": str(e)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    out = run()
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 0 if out.get("result") in ("COMPLETE", "INCOMPLETE_BUDGET") else 2


if __name__ == "__main__":
    sys.exit(main())
