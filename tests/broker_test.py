"""Offline tests for keys/inject.py (run inside the keys image, see tests/broker.sh)."""
import base64
import json
import os
import sys
import tempfile

from mitmproxy.test import tflow, tutils


def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def jwt(payload, sig):
    return f"{b64({'alg': 'RS256'})}.{b64(payload)}.{sig}"


tmp = tempfile.mkdtemp()
os.makedirs(f"{tmp}/t")
REAL = {
    "OPENAI_API_KEY": None,
    "tokens": {
        "id_token": jwt({"email": "me@x", "chatgpt_plan_type": "plus"}, "REALSIGID"),
        "access_token": jwt({"exp": 1}, "REALSIGACCESS"),
        "refresh_token": "rt_REALREFRESH",
        "account_id": "acc-1",
    },
    "last_refresh": "2026-10-01T00:00:00Z",
}
json.dump(REAL, open(f"{tmp}/t/codex-auth.json", "w"))
open(f"{tmp}/t/anthropic", "w").write("sk-ant-oat01-REALCLAUDE\n")
json.dump({
    "services": {
        "anthropic": {"hosts": ["api.anthropic.com"], "placeholder": "sk-ant-oat01-sbx-placeholder"},
        "codex": {"kind": "codex", "hosts": ["chatgpt.com", "auth.openai.com"]},
    },
    "agents": {"t": {"listen": "x", "upstream": "x", "services": ["anthropic", "codex"]}},
}, open(f"{tmp}/policy.json", "w"))
os.environ.update(SBX_AGENT="t", SBX_POLICY=f"{tmp}/policy.json", SBX_SECRETS=tmp)
sys.path.insert(0, "/opt/keys")
import codexauth  # noqa: E402
import inject  # noqa: E402

FAKE = codexauth.fake_auth(REAL)["tokens"]
fails = 0


def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    fails += not cond


def flow(host, path="/", headers=(), body=b"", method="GET"):
    f = tflow.tflow(req=tutils.treq(host=host, port=443, scheme=b"https", path=path.encode(),
                                    method=method.encode(), headers=list(headers), content=body))
    f.request.host_header = host
    return f


# Claude: placeholder → real only in the auth header
f = flow("api.anthropic.com", "/v1/messages?k=sk-ant-oat01-sbx-placeholder",
         [(b"authorization", b"Bearer sk-ant-oat01-sbx-placeholder")], b"sk-ant-oat01-sbx-placeholder", "POST")
inject.request(f)
check("claude: auth header gets real token", f.request.headers["authorization"] == "Bearer sk-ant-oat01-REALCLAUDE")
check("claude: body untouched", f.request.content == b"sk-ant-oat01-sbx-placeholder")
check("claude: query untouched", "sbx-placeholder" in f.request.path)

# Codex: fake id/access tokens in auth.json look like JWTs but carry no real signature
check("codex: agent's tokens contain no real signature",
      not any(s in json.dumps(FAKE) for s in ("REALSIGID", "REALSIGACCESS", "rt_REALREFRESH")))
check("codex: claims preserved for the CLI", FAKE["id_token"].split(".")[1] == REAL["tokens"]["id_token"].split(".")[1])

f = flow("chatgpt.com", "/backend-api/codex/responses", [(b"authorization", f"Bearer {FAKE['access_token']}".encode())])
inject.request(f)
check("codex: API call gets real access token", f.request.headers["authorization"] == f"Bearer {REAL['tokens']['access_token']}")

# Codex refresh: fake refresh in body → real; response tokens saved, agent sees fakes
f = flow("auth.openai.com", "/oauth/token", [(b"content-type", b"application/json")],
         json.dumps({"grant_type": "refresh_token", "refresh_token": FAKE["refresh_token"]}).encode(), "POST")
inject.request(f)
check("codex refresh: request carries real refresh token", "rt_REALREFRESH" in f.request.get_text())
NEW = {"id_token": jwt({"email": "me@x"}, "NEWSIGID"), "access_token": jwt({"exp": 2}, "NEWSIGACCESS"),
       "refresh_token": "rt_NEWREFRESH"}
f.response = tutils.tresp(status_code=200, content=json.dumps(NEW).encode())
inject.responseheaders(f)
inject.response(f)
out = f.response.get_text()
check("codex refresh: agent gets no real token back", not any(s in out for s in ("NEWSIGID", "NEWSIGACCESS", "rt_NEWREFRESH")))
saved = json.load(open(f"{tmp}/t/codex-auth.json"))["tokens"]
check("codex refresh: broker saved new real tokens", saved["refresh_token"] == "rt_NEWREFRESH")
new_fake_access = json.loads(out)["access_token"]
f = flow("chatgpt.com", "/backend-api/codex/responses", [(b"authorization", f"Bearer {new_fake_access}".encode())])
inject.request(f)
check("codex: new fake maps to new real access token", f.request.headers["authorization"] == f"Bearer {NEW['access_token']}")

# A fake sent to some other host is not swapped (host is not even intercepted)
f = flow("evil.example", "/", [(b"authorization", f"Bearer {new_fake_access}".encode())])
inject.request(f)
check("other host: nothing swapped", f.request.headers["authorization"] == f"Bearer {new_fake_access}")

print(f"broker: {'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
