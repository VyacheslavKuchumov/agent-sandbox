#!/usr/bin/env python3
"""Check / bump pinned component versions.

Only stable releases at least --min-age days old are considered (fresh releases
are where supply-chain surprises live). Usage:
  versions.py check [--min-age N]
  versions.py bump  [--min-age N]   # rewrites pins in Dockerfiles
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def get(url):
    headers = {"User-Agent": "agent-sandbox"}
    if "api.github.com" in url and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def gh_release(repo, cutoff):
    rels = get(f"https://api.github.com/repos/{repo}/releases?per_page=30")
    ok = [r for r in rels if not r["prerelease"] and not r["draft"] and ts(r["published_at"]) <= cutoff]
    return max(ok, key=lambda r: ts(r["published_at"]))["tag_name"] if ok else None


def npm(pkg, cutoff):
    d = get(f"https://registry.npmjs.org/{pkg}")
    ok = [(v, ts(t)) for v, t in d["time"].items()
          if v not in ("created", "modified") and v in d["versions"] and "-" not in v and ts(t) <= cutoff]
    return max(ok, key=lambda x: x[1])[0] if ok else None


def hub_tag(repo, pattern, cutoff):
    d = get(f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated")
    rx = re.compile(pattern)
    ok = [(r["name"], ts(r["last_updated"])) for r in d["results"] if rx.fullmatch(r["name"])]
    # Docker Hub has no "created" for tags; use the version number, filtered by age.
    ok = [n for n, t in ok if t <= cutoff] or []
    key = lambda n: tuple(int(x) for x in re.findall(r"\d+", n))
    return max(ok, key=key) if ok else None


# file, regex with one group = current version, how to find the latest
PINS = [
    ("sing-box", "vpn/Dockerfile", r"FROM ghcr\.io/sagernet/sing-box:v([\d.]+)",
     lambda c: (gh_release("SagerNet/sing-box", c) or "").lstrip("v")),
    ("zashboard", "vpn/Dockerfile", r"ARG ZASHBOARD_VERSION=v([\d.]+)",
     lambda c: (gh_release("Zephyruso/zashboard", c) or "").lstrip("v")),
    ("mitmproxy", "keys/Dockerfile", r"FROM mitmproxy/mitmproxy:([\d.]+)",
     lambda c: hub_tag("mitmproxy/mitmproxy", r"\d+\.\d+\.\d+", c)),
    ("node (24 LTS)", "agents/base/Dockerfile", r"FROM node:([\d.]+)-trixie-slim",
     lambda c: (hub_tag("library/node", r"24\.\d+\.\d+-trixie-slim", c) or "").replace("-trixie-slim", "")),
    ("claude-code", "agents/base/Dockerfile", r"ARG CLAUDE_CODE_VERSION=([\d.]+)",
     lambda c: npm("@anthropic-ai/claude-code", c)),
    ("codex", "agents/base/Dockerfile", r"ARG CODEX_VERSION=([\d.]+)",
     lambda c: npm("@openai/codex", c)),
    ("opencode", "agents/opencode/Dockerfile", r"ARG OPENCODE_VERSION=([\d.]+)",
     lambda c: npm("opencode-ai", c)),
    ("python (mailgw)", "mailgw/Dockerfile", r"FROM python:([\d.]+)-slim-trixie",
     lambda c: (hub_tag("library/python", r"3\.13\.\d+-slim-trixie", c) or "").replace("-slim-trixie", "")),
    ("socket-proxy", "compose.yaml", r"tecnativa/docker-socket-proxy:v([\d.]+)",
     lambda c: (gh_release("Tecnativa/docker-socket-proxy", c) or "").lstrip("v")),
    ("grafana", "compose.yaml", r"grafana/grafana:([\d.]+)",
     lambda c: (gh_release("grafana/grafana", c) or "").lstrip("v")),
    ("loki", "compose.yaml", r"grafana/loki:([\d.]+)",
     lambda c: hub_tag("grafana/loki", r"3\.\d+\.\d+", c)),
    ("vector", "compose.yaml", r"timberio/vector:([\d.]+)-alpine",
     lambda c: (hub_tag("timberio/vector", r"0\.\d+\.\d+-alpine", c) or "").replace("-alpine", "")),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["check", "bump"])
    ap.add_argument("--min-age", type=int, default=14)
    a = ap.parse_args()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.min_age)

    changed = 0
    print(f"{'component':16} {'pinned':12} {'available':12}  (stable, ≥{a.min_age} days old)")
    for name, rel, rx, latest_fn in PINS:
        path = ROOT / rel
        text = path.read_text()
        m = re.search(rx, text)
        cur = m.group(1) if m else "?"
        try:
            new = latest_fn(cutoff) or cur
        except Exception as e:
            print(f"{name:16} {cur:12} error: {e}")
            continue
        mark = "" if new == cur else "  ← update"
        print(f"{name:16} {cur:12} {new:12}{mark}")
        if a.action == "bump" and new != cur and m:
            path.write_text(re.sub(rx, lambda mm: mm.group(0).replace(mm.group(1), new), text))
            changed += 1
    if a.action == "bump":
        print(f"bumped {changed} pin(s)")
    sys.exit(0)


if __name__ == "__main__":
    main()
