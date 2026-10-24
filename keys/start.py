#!/usr/bin/env python3
"""Start one mitmdump per agent from policy.json; exit if any of them dies."""
import json
import os
import re
import signal
import subprocess
import sys
import time

POLICY = json.load(open(os.environ.get("SBX_POLICY", "/opt/keys/policy.json")))
CONFDIR = os.environ.get("SBX_CA_DIR", "/run/ca")
PORT = os.environ.get("SBX_PORT", "8080")

procs = []
for agent, cfg in POLICY["agents"].items():
    hosts = sorted({h for s in cfg["services"] for h in POLICY["services"][s]["hosts"]})
    allow = "^(" + "|".join(re.escape(h) for h in hosts) + r")(:\d+)?$"
    cmd = [
        "mitmdump", "--quiet",
        "--mode", f"upstream:{cfg['upstream']}",
        "--listen-host", cfg["listen"], "--listen-port", PORT,
        "--set", f"confdir={CONFDIR}",
        "--set", f"allow_hosts={allow}",
        "--set", "connection_strategy=lazy",
        "-s", "/opt/keys/inject.py",
    ]
    env = dict(os.environ, SBX_AGENT=agent)
    procs.append((agent, subprocess.Popen(cmd, env=env)))
    print(f"keys: {agent} on {cfg['listen']}:{PORT} → {cfg['upstream']}, intercept {hosts}", flush=True)


def stop(*_):
    for _, p in procs:
        p.terminate()
    sys.exit(0)


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while True:
    for agent, p in procs:
        if p.poll() is not None:
            print(f"keys: {agent} exited with {p.returncode}", file=sys.stderr, flush=True)
            for _, q in procs:
                q.terminate()
            sys.exit(1)
    time.sleep(1)
