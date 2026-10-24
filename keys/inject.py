"""mitmproxy addon: swap placeholder credentials for real ones.

One mitmdump process per agent (see start.py). Only hosts from policy.json are
intercepted (allow_hosts); everything else is tunneled untouched. Secrets are
inserted only into the Authorization / x-api-key header of a request to a host
of the same service — never into URLs or bodies, so an agent cannot trick the
broker into writing a token somewhere it can read it back. The one exception is
the codex OAuth token endpoint, whose response is rewritten back to fakes.
"""
import base64
import json
import os
import sys
import time

from mitmproxy import http

sys.path.insert(0, os.path.dirname(__file__))
import codexauth  # noqa: E402

AGENT = os.environ["SBX_AGENT"]
POLICY = json.load(open(os.environ.get("SBX_POLICY", "/opt/keys/policy.json")))
SECRETS_DIR = os.environ.get("SBX_SECRETS", "/run/keys")
AUTH_HEADERS = ("authorization", "x-api-key")
CODEX_TOKEN_PATH = "/oauth/token"


def warn(msg):
    print(f"keys[{AGENT}]: {msg}", file=sys.stderr, flush=True)


class Codex:
    """Real ChatGPT tokens for codex, persisted across refreshes."""

    def __init__(self, path):
        self.path = path
        self.auth = None
        self.map = {}
        self.load()

    def load(self):
        try:
            self.auth = json.load(open(self.path))
            self.map = codexauth.fake_to_real(self.auth)
        except (OSError, ValueError):
            self.auth, self.map = None, {}

    def save_tokens(self, fresh):
        tokens = self.auth.setdefault("tokens", {})
        for k in codexauth.TOKEN_FIELDS:
            if fresh.get(k):
                tokens[k] = fresh[k]
        self.auth["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.auth, f, indent=2)
        os.replace(tmp, self.path)
        self.map = codexauth.fake_to_real(self.auth)

    def to_real(self, text):
        for fake, real in self.map.items():
            if fake in text:
                text = text.replace(fake, real)
        return text


def load_services():
    """host -> (service, placeholder, secret_or_Codex) for services this agent may use."""
    by_host = {}
    for name in POLICY["agents"][AGENT]["services"]:
        svc = POLICY["services"][name]
        if svc.get("kind") == "codex":
            secret = Codex(os.path.join(SECRETS_DIR, AGENT, "codex-auth.json"))
            if not secret.auth:
                warn(f"no codex login ({secret.path}) — run: bin/sandbox login {AGENT} codex")
        else:
            path = os.path.join(SECRETS_DIR, AGENT, name)
            try:
                secret = open(path).read().strip()
            except OSError:
                secret = ""
            if not secret:
                warn(f"no secret for {name} ({path}) — requests will stay unauthenticated")
        for host in svc["hosts"]:
            by_host[host] = (name, svc.get("placeholder", ""), secret)
    return by_host


SERVICES = load_services()


def real_secrets():
    out = []
    for _, _, s in SERVICES.values():
        if isinstance(s, Codex):
            out += list(s.map.values())
        elif s:
            out.append(s)
    return out


def swap(value, placeholder, secret):
    if isinstance(secret, Codex):
        return secret.to_real(value)
    if placeholder in value:
        return value.replace(placeholder, secret)
    if value[:6].lower() == "basic ":
        try:
            raw = base64.b64decode(value[6:]).decode()
        except Exception:
            return value
        if placeholder in raw:
            return "Basic " + base64.b64encode(raw.replace(placeholder, secret).encode()).decode()
    return value


def is_codex_token_call(flow, secret):
    return isinstance(secret, Codex) and flow.request.path.split("?", 1)[0] == CODEX_TOKEN_PATH


def log(flow, service, injected):
    print(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "agent": AGENT,
        "service": service,
        "injected": injected,
        "method": flow.request.method,
        "host": flow.request.pretty_host,
        "path": flow.request.path.split("?", 1)[0],
        "status": flow.response.status_code if flow.response else None,
    }), flush=True)


def request(flow: http.HTTPFlow):
    entry = SERVICES.get(flow.request.pretty_host)
    if not entry:
        return
    service, placeholder, secret = entry
    injected = False
    if secret:
        for h in AUTH_HEADERS:
            if h in flow.request.headers:
                new = swap(flow.request.headers[h], placeholder, secret)
                if new != flow.request.headers[h]:
                    flow.request.headers[h] = new
                    injected = True
        if is_codex_token_call(flow, secret) and flow.request.content:
            body = flow.request.get_text(strict=False)
            new = secret.to_real(body)
            if new != body:
                flow.request.set_text(new)
                injected = True
    flow.metadata["sbx"] = (service, injected, secret)


def responseheaders(flow: http.HTTPFlow):
    meta = flow.metadata.get("sbx")
    # Token endpoint responses are rewritten (see response); everything else streams.
    if not (meta and is_codex_token_call(flow, meta[2])):
        flow.response.stream = True
    secrets = real_secrets()
    for k, v in list(flow.response.headers.items()):
        for s in secrets:
            if s in v:
                flow.response.headers[k] = v.replace(s, "[redacted]")


def response(flow: http.HTTPFlow):
    meta = flow.metadata.get("sbx")
    if not meta:
        return
    service, injected, secret = meta
    if is_codex_token_call(flow, secret):
        try:
            data = json.loads(flow.response.get_text(strict=False) or "{}")
        except ValueError:
            data = None
        if isinstance(data, dict):
            if flow.response.status_code == 200 and secret.auth:
                fresh = {k: data[k] for k in codexauth.TOKEN_FIELDS if data.get(k)}
                if fresh:
                    secret.save_tokens(fresh)
            for k in list(data):
                if isinstance(data[k], str) and (k.endswith("token") or k == "api_key"):
                    data[k] = codexauth.fake(data[k])
            flow.response.set_text(json.dumps(data))
        else:
            flow.response.set_text("")  # never hand an unparsed token response to the agent
    log(flow, service, injected)
