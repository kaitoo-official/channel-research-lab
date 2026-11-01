"""HTTP transport for ytca.api (YouTube Data API v3). Owner approval is required before any real use.

- Only two URLs can be called (channels, playlistItems); anything else raises.
- `fields` limits the response to the values we keep.
- The API key comes from the environment variable CRL_YOUTUBE_API_KEY (set by the owner). It is never written to
  disk, logs, exceptions or repr. Because the key travels in the query string, request URLs are never logged.
- One request per call: no automatic retry, no backoff loop. Errors are returned as {"error": {...}} without the URL.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

ENV_KEY = "CRL_YOUTUBE_API_KEY"
BASE = "https://www.googleapis.com/youtube/v3/"
ALLOWED = {"channels.list": BASE + "channels", "playlistItems.list": BASE + "playlistItems"}
ALLOWED_PARAMS = {
    "channels.list": {"part", "forHandle", "id"},
    "playlistItems.list": {"part", "playlistId", "maxResults", "pageToken"},
}
FIELDS = {
    "channels.list": "items(id,contentDetails/relatedPlaylists/uploads)",
    "playlistItems.list": "nextPageToken,pageInfo/totalResults,items(contentDetails(videoId,videoPublishedAt),snippet(title,resourceId/videoId))",
}
TIMEOUT = 20


class TransportError(Exception):
    """Carries only a code; never the URL or the key."""


class Transport:
    def __init__(self, env=None, opener=None, timeout=TIMEOUT):
        env = os.environ if env is None else env
        key = (env.get(ENV_KEY) or "").strip()
        if not key:
            raise TransportError(f"MISSING_API_KEY: set the environment variable {ENV_KEY}")
        self.__key = key
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout
        self.calls = 0

    def __repr__(self):
        return "Transport(key=***)"

    def __call__(self, endpoint, params):
        if endpoint not in ALLOWED:
            raise TransportError(f"ENDPOINT_NOT_ALLOWED: {endpoint}")
        extra = set(params) - ALLOWED_PARAMS[endpoint]
        if extra:
            raise TransportError(f"PARAM_NOT_ALLOWED: {sorted(extra)}")
        query = urllib.parse.urlencode({**params, "fields": FIELDS[endpoint], "key": self.__key})
        req = urllib.request.Request(ALLOWED[endpoint] + "?" + query, headers={"Accept": "application/json"})
        self.calls += 1
        try:
            with self._open(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
                err = body.get("error") or {}
                reasons = [{"reason": x.get("reason")} for x in (err.get("errors") or [])]
                return {"error": {"code": e.code, "errors": reasons or [{"reason": f"HTTP_{e.code}"}]}}
            except Exception:
                return {"error": {"code": e.code, "errors": [{"reason": f"HTTP_{e.code}"}]}}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TransportError(f"NETWORK: {type(e).__name__}") from None
