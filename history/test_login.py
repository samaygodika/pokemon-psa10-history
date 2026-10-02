#!/usr/bin/env python3
"""Checks for the scraper's alt.xyz login (2026-10-02): the Bearer JWT is minted from the
session token, refreshed before it expires, re-minted once on a 401/403, and a run with no
token fails with the help text instead of retrying into the dark.

    python3 history/test_login.py

Plain asserts, no pytest. urlopen is monkeypatched: nothing here talks to alt.xyz or Stytch."""
import base64
import io
import json
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import alt_scraper as s   # noqa: E402

GOOD = {"data": {"asset": {"id": "x", "name": "card"}}}


def jwt_with_exp(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{payload}.sig"


class Resp(io.BytesIO):
    status = 200

    def __enter__(self): return self
    def __exit__(self, *a): return False


class Fake:
    """Stands in for urllib.request.urlopen. `plan` maps a prefix of the URL to a list of
    outcomes, popped in order: a dict is returned as JSON, an int raises that HTTP status."""

    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url.rsplit("/", 1)[-1], dict(req.headers)))
        for prefix, outcomes in self.plan.items():
            if url.startswith(prefix):
                out = outcomes.pop(0)
                if isinstance(out, int):
                    raise urllib.error.HTTPError(url, out, "err", {}, io.BytesIO(b"Forbidden"))
                return Resp(json.dumps(out).encode())
        raise AssertionError(f"unexpected url {url}")


def fresh_session(token):
    sess = s._Session()
    sess._resolved, sess.token = True, token
    return sess


def stytch_ok(exp, session_exp="2027-10-02T21:11:29Z"):
    return {"data": {"session_jwt": jwt_with_exp(exp), "session": {"expires_at": session_exp}}}


s.DELAY_SECONDS = 0
s.time.sleep = lambda *_: None

# 1. First call mints a JWT, sends it as Bearer; second call within the JWT's life reuses it.
s.SESSION = fresh_session("tok123")
fake = Fake({s.STYTCH_AUTH_URL: [stytch_ok(time.time() + 300)], s.ENDPOINT: [GOOD, GOOD]})
s.urllib.request.urlopen = fake
assert s.gql("AssetInfo", s.Q_ASSET, {"id": "x"}) == GOOD["data"]
assert s.gql("AssetInfo", s.Q_ASSET, {"id": "x"}) == GOOD["data"]
ops = [c[0] for c in fake.calls]
assert ops == ["authenticate", "AssetInfo", "AssetInfo"], ops
auth_hdr = fake.calls[0][1]["Authorization"]
assert auth_hdr == "Basic " + base64.b64encode(f"{s.STYTCH_PUBLIC_TOKEN}:tok123".encode()).decode()
assert fake.calls[1][1]["Authorization"].startswith("Bearer h."), fake.calls[1][1]
assert fake.calls[0][1]["X-sdk-client"], "x-sdk-client header missing"
print("mint + reuse ok")

# 2. A JWT inside the refresh margin is replaced before the next call.
s.SESSION = fresh_session("tok123")
fake = Fake({s.STYTCH_AUTH_URL: [stytch_ok(time.time() + 30), stytch_ok(time.time() + 300)], s.ENDPOINT: [GOOD, GOOD]})
s.urllib.request.urlopen = fake
s.gql("AssetInfo", s.Q_ASSET, {"id": "x"}); s.gql("AssetInfo", s.Q_ASSET, {"id": "x"})
ops = [c[0] for c in fake.calls]
assert ops == ["authenticate", "AssetInfo", "authenticate", "AssetInfo"], ops
print("refresh before expiry ok")

# 3. A 403 with a live JWT re-mints once and retries the same operation.
s.SESSION = fresh_session("tok123")
fake = Fake({s.STYTCH_AUTH_URL: [stytch_ok(time.time() + 300), stytch_ok(time.time() + 300)], s.ENDPOINT: [403, GOOD]})
s.urllib.request.urlopen = fake
assert s.gql("AssetInfo", s.Q_ASSET, {"id": "x"}) == GOOD["data"]
ops = [c[0] for c in fake.calls]
assert ops == ["authenticate", "AssetInfo", "authenticate", "AssetInfo"], ops
print("re-mint on 403 ok")

# 4. No token: the first 403 fails with the help text and no retry.
s.SESSION = fresh_session(None)
fake = Fake({s.ENDPOINT: [403, 403, 403]})
s.urllib.request.urlopen = fake
try:
    s.gql("AssetInfo", s.Q_ASSET, {"id": "x"})
    raise AssertionError("expected a RuntimeError")
except RuntimeError as e:
    assert "no login configured" in str(e) and "ALT_SESSION_TOKEN" in str(e), e
assert len(fake.calls) == 1, fake.calls
assert fake.calls[0][1]["Authorization"] == "", "logged-out request must send an empty authorization header"
print("no-token failure ok")

# 5. Stytch rejects the session token: a clear error, not a 403 loop.
s.SESSION = fresh_session("stale")
fake = Fake({s.STYTCH_AUTH_URL: [401]})
s.urllib.request.urlopen = fake
try:
    s.gql("AssetInfo", s.Q_ASSET, {"id": "x"})
    raise AssertionError("expected a RuntimeError")
except RuntimeError as e:
    assert "login refresh failed: HTTP 401" in str(e), e
print("bad session token ok")

# 6. Token file: first non-blank line, env var wins.
import os, tempfile
with tempfile.TemporaryDirectory() as d:
    f = Path(d) / "sess"; f.write_text("\n  filetok  \nsecond\n")
    s.SESSION_FILE = f
    os.environ.pop("ALT_SESSION_TOKEN", None)
    assert s._Session().session_token() == "filetok"
    os.environ["ALT_SESSION_TOKEN"] = "envtok"
    assert s._Session().session_token() == "envtok"
    os.environ.pop("ALT_SESSION_TOKEN", None)
print("token sources ok")
print("all login checks passed")
