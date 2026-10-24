"""Offline tests for keys/inject.py (run inside the keys image, see tests/broker.sh)."""
import asyncio
import base64
import importlib.util
import json
import os
import sys
import tempfile
import time

from mitmproxy.test import tflow, tutils


def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def jwt(payload, sig):
    return f"{b64({'alg': 'RS256'})}.{b64(payload)}.{sig}"


tmp = tempfile.mkdtemp()
for a in ("t", "u"):
    os.makedirs(f"{tmp}/{a}")
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
# One ChatGPT login shared by every agent's broker process.
json.dump(REAL, open(f"{tmp}/codex-auth.json", "w"))
open(f"{tmp}/t/anthropic", "w").write("sk-ant-oat01-REALCLAUDE\n")
json.dump({
    "services": {
        "anthropic": {"hosts": ["api.anthropic.com"], "placeholder": "sk-ant-oat01-sbx-placeholder"},
        "codex": {"kind": "codex", "hosts": ["chatgpt.com", "auth.openai.com"]},
    },
    "agents": {a: {"listen": "x", "upstream": "x", "services": ["anthropic", "codex"]} for a in ("t", "u")},
}, open(f"{tmp}/policy.json", "w"))
os.environ.update(SBX_POLICY=f"{tmp}/policy.json", SBX_SECRETS=tmp)
sys.path.insert(0, "/opt/keys")
import codexauth  # noqa: E402


def load_agent(name):
    """A separate copy of the addon, like the broker's separate mitmdump process per agent."""
    os.environ["SBX_AGENT"] = name
    spec = importlib.util.spec_from_file_location(f"inject_{name}", "/opt/keys/inject.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


inject = load_agent("t")
other = load_agent("u")

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


def req(mod, f):
    asyncio.run(mod.request(f))
    return f


def api(mod, access):
    return req(mod, flow("chatgpt.com", "/backend-api/codex/responses", [(b"authorization", f"Bearer {access}".encode())]))


def refresh(refresh_token):
    return flow("auth.openai.com", "/oauth/token", [(b"content-type", b"application/json")],
                json.dumps({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode(), "POST")


def answer(mod, f, tokens):
    f.response = tutils.tresp(status_code=200, content=json.dumps(tokens).encode())
    mod.responseheaders(f)
    mod.response(f)
    return json.loads(f.response.get_text())


def saved():
    return json.load(open(f"{tmp}/codex-auth.json"))["tokens"]


# Claude: placeholder → real only in the auth header
f = req(inject, flow("api.anthropic.com", "/v1/messages?k=sk-ant-oat01-sbx-placeholder",
                     [(b"authorization", b"Bearer sk-ant-oat01-sbx-placeholder")], b"sk-ant-oat01-sbx-placeholder", "POST"))
check("claude: auth header gets real token", f.request.headers["authorization"] == "Bearer sk-ant-oat01-REALCLAUDE")
check("claude: body untouched", f.request.content == b"sk-ant-oat01-sbx-placeholder")
check("claude: query untouched", "sbx-placeholder" in f.request.path)

# Codex: fake id/access tokens in auth.json look like JWTs but carry no real signature
check("codex: agent's tokens contain no real signature",
      not any(s in json.dumps(FAKE) for s in ("REALSIGID", "REALSIGACCESS", "rt_REALREFRESH")))
check("codex: claims preserved for the CLI", FAKE["id_token"].split(".")[1] == REAL["tokens"]["id_token"].split(".")[1])

f = api(inject, FAKE["access_token"])
check("codex: API call gets real access token", f.request.headers["authorization"] == f"Bearer {REAL['tokens']['access_token']}")

# Codex refresh: fake refresh in body → real; response tokens saved, agent sees fakes
f = req(inject, refresh(FAKE["refresh_token"]))
check("codex refresh: request carries real refresh token", "rt_REALREFRESH" in f.request.get_text())
NEW = {"id_token": jwt({"email": "me@x"}, "NEWSIGID"), "access_token": jwt({"exp": int(time.time()) + 3600}, "NEWSIGACCESS"),
       "refresh_token": "rt_NEWREFRESH"}
out = answer(inject, f, NEW)
check("codex refresh: agent gets no real token back", not any(s in json.dumps(out) for s in ("NEWSIGID", "NEWSIGACCESS", "rt_NEWREFRESH")))
check("codex refresh: broker saved new real tokens", saved()["refresh_token"] == "rt_NEWREFRESH")
f = api(inject, out["access_token"])
check("codex: new fake maps to new real access token", f.request.headers["authorization"] == f"Bearer {NEW['access_token']}")

# Shared login: the other agent still holds fakes from before the rotation
f = api(other, FAKE["access_token"])
check("shared: stale fake of another agent → current access token", f.request.headers["authorization"] == f"Bearer {NEW['access_token']}")
f = req(other, refresh(FAKE["refresh_token"]))
check("shared: stale refresh answered by the broker, not upstream", f.response is not None and f.response.status_code == 200)
other.response(f)
local = json.loads(f.response.get_text())
check("shared: it gets fakes of the current tokens", local.get("refresh_token") == codexauth.fake("rt_NEWREFRESH"))
check("shared: no real token in the local answer", not any(s in f.response.get_text() for s in ("NEWSIGID", "NEWSIGACCESS", "rt_NEWREFRESH")))
check("shared: refresh token not spent again", saved()["refresh_token"] == "rt_NEWREFRESH")


# Concurrent refresh by two agents: the second waits for the first and reuses its result
async def race():
    first = refresh(out["refresh_token"])
    await inject.request(first)
    check("race: first refresh goes upstream with real token", "rt_NEWREFRESH" in first.request.get_text())
    second = refresh(local["refresh_token"])
    task = asyncio.create_task(other.request(second))
    await asyncio.sleep(0.3)
    check("race: second refresh waits for the lock", not task.done())
    NEW2 = {"id_token": jwt({"email": "me@x"}, "N2ID"), "access_token": jwt({"exp": int(time.time()) + 3600}, "N2ACCESS"),
            "refresh_token": "rt_NEW2"}
    answer(inject, first, NEW2)
    await asyncio.wait_for(task, 5)
    check("race: second answered locally with the fresh tokens",
          second.response is not None and json.loads(second.response.get_text())["refresh_token"] == codexauth.fake("rt_NEW2"))
    check("race: rt_NEW2 saved once", saved()["refresh_token"] == "rt_NEW2")

asyncio.run(race())

# Stale fake but the current access token is about to expire: refresh upstream with the current real one
tok = json.load(open(f"{tmp}/codex-auth.json"))
tok["tokens"]["access_token"] = jwt({"exp": 1}, "N2ACCESS")
json.dump(tok, open(f"{tmp}/codex-auth.json", "w"))
os.utime(f"{tmp}/codex-auth.json", ns=(time.time_ns() + 10**9,) * 2)
f = req(other, refresh(FAKE["refresh_token"]))
check("expiring: stale fake → upstream with current real refresh", f.response is None and "rt_NEW2" in f.request.get_text())
f.response = tutils.tresp(status_code=500, content=b"{}")
other.response(f)
check("expiring: lock released after an upstream error", asyncio.run(inject.Codex(f"{tmp}/codex-auth.json").lock(1)) is not None)

# A fake sent to some other host is not swapped (host is not even intercepted)
f = req(inject, flow("evil.example", "/", [(b"authorization", f"Bearer {out['access_token']}".encode())]))
check("other host: nothing swapped", f.request.headers["authorization"] == f"Bearer {out['access_token']}")

print(f"broker: {'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
