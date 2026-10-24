"""mitmproxy addon: swap placeholder credentials for real ones.

One mitmdump process per agent (see start.py). Only hosts from policy.json are
intercepted (allow_hosts); everything else is tunneled untouched. The secret is
inserted only into the Authorization / x-api-key header of a request to a host
of the same service — never into URLs or bodies, so an agent cannot trick the
broker into writing a token somewhere it can read it back.
"""
import base64
import json
import os
import sys
import time

from mitmproxy import http

AGENT = os.environ["SBX_AGENT"]
POLICY = json.load(open(os.environ.get("SBX_POLICY", "/opt/keys/policy.json")))
SECRETS_DIR = os.environ.get("SBX_SECRETS", "/run/keys")
AUTH_HEADERS = ("authorization", "x-api-key")


def load_services():
    """host -> (service, placeholder, secret) for services this agent may use."""
    by_host = {}
    for name in POLICY["agents"][AGENT]["services"]:
        svc = POLICY["services"][name]
        path = os.path.join(SECRETS_DIR, AGENT, name)
        try:
            secret = open(path).read().strip()
        except OSError:
            secret = ""
        if not secret:
            print(f"keys[{AGENT}]: no secret for {name} ({path}) — requests will stay unauthenticated",
                  file=sys.stderr)
        for host in svc["hosts"]:
            by_host[host] = (name, svc["placeholder"], secret)
    return by_host


SERVICES = load_services()
ALL_SECRETS = [s for _, _, s in SERVICES.values() if s]


def swap(value, placeholder, secret):
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
    flow.metadata["sbx"] = (service, injected)


def responseheaders(flow: http.HTTPFlow):
    # Stream everything (SSE from the LLM APIs) and make sure no real secret is
    # echoed back to the agent in response headers.
    flow.response.stream = True
    for k, v in list(flow.response.headers.items()):
        for s in ALL_SECRETS:
            if s in v:
                flow.response.headers[k] = v.replace(s, "[redacted]")


def response(flow: http.HTTPFlow):
    if "sbx" in flow.metadata:
        log(flow, *flow.metadata["sbx"])
