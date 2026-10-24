#!/bin/sh
# vpn container: config → kill switch firewall → sing-box (unprivileged).
set -eu

RUN_DIR=/run/sing-box
APP_UID=1000:1000
PROXY_PORT=${PROXY_PORT:-7890}
DASH_PORT=${CONTROLLER##*:}
HOST_PROXY=${HOST_PROXY:-10.88.0.2:7891}

die() { echo "entrypoint: $*" >&2; exit 1; }

# 1. Config is generated unprivileged (secrets are owned by the host user).
su-exec "$APP_UID" python3 /opt/vpn/gen-config.py
. "$RUN_DIR/server.env"

# 2. Interfaces: egress = the one with a default route, everything else = agent nets.
EGRESS_IF=$(ip -4 route show default | awk '{print $5; exit}')
[ -n "$EGRESS_IF" ] || die "no default route — egress network not attached"
AGENT_IFS=$(ip -o -4 addr show | awk -v e="$EGRESS_IF" '$2 != "lo" && $2 != e {printf "%s\"%s\"", sep, $2; sep=", "}')
[ -n "$AGENT_IFS" ] || die "no agent networks attached"

sed -e "s/@AGENT_IFS@/$AGENT_IFS/" \
    -e "s/@EGRESS_IF@/$EGRESS_IF/g" \
    -e "s/@PROXY_PORT@/$PROXY_PORT/g" \
    -e "s/@DASH_PORT@/$DASH_PORT/g" \
    -e "s/@HOST_PROXY_PORT@/${HOST_PROXY##*:}/g" \
    -e "s/@HOST_WG_PORT@/${HOST_WG_PORT:-51820}/g" \
    -e "s/@SERVER_IP@/$SERVER_IP/g" \
    -e "s/@SERVER_PORT@/$SERVER_PORT/g" \
    /opt/vpn/killswitch.nft.tmpl > /tmp/killswitch.nft
nft -f /tmp/killswitch.nft || die "failed to apply kill switch"
echo "entrypoint: kill switch on (egress=$EGRESS_IF → $SERVER_IP:$SERVER_PORT only; agents=$AGENT_IFS)"

# 3. sing-box runs as an unprivileged user.
su-exec "$APP_UID" sing-box check -c "$RUN_DIR/config.json"
exec su-exec "$APP_UID" sing-box run -c "$RUN_DIR/config.json" -D "$RUN_DIR"
