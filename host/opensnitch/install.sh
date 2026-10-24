#!/bin/sh
# Install OpenSnitch in monitoring mode and forward its events to the sandbox
# monitoring stack (Vector on 127.0.0.1:5140/udp → Loki → Grafana).
# Run as root:  sudo sh host/opensnitch/install.sh
set -eu
cd "$(dirname "$0")"
pacman -Q opensnitch >/dev/null 2>&1 || pacman -S --needed opensnitch
CFG=/etc/opensnitchd/default-config.json
cp -a "$CFG" "$CFG.bak.$(date +%Y%m%d%H%M%S)"
python3 -I - "$CFG" config-overrides.json <<'PY'
import json, sys
cfg, ov = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
ov.pop("_doc", None)
for k, v in ov.items():
    if isinstance(v, dict):
        cfg.setdefault(k, {}).update(v)
    else:
        cfg[k] = v
json.dump(cfg, open(sys.argv[1], "w"), indent=2)
PY
install -m 644 rules/*.json /etc/opensnitchd/rules/
systemctl enable --now opensnitchd
systemctl restart opensnitchd
echo "OpenSnitch: monitoring mode (default allow), events → 127.0.0.1:5140/udp."
echo "GUI: opensnitch-ui (disable pop-ups in its preferences)."
