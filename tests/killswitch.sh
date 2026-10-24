#!/bin/sh
# Kill switch checks. Probes run in throwaway containers on each agent network,
# exactly where agents will live. Usage: tests/killswitch.sh
set -u
cd "$(dirname "$0")/.."

IMG=agent-sandbox/vpn:local
PASS=0; FAIL=0
ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
bad()  { echo "  ✗ $*"; FAIL=$((FAIL+1)); }
probe() { net=$1; shift; docker run --rm --network "$net" --entrypoint sh "$IMG" -c "$*" >/dev/null 2>&1; }

docker inspect sbx-vpn >/dev/null 2>&1 || { echo "sbx-vpn is not running"; exit 1; }
HOST_IP=$(curl -s --max-time 5 https://ifconfig.me || echo "?")

for n in coder infra admin web; do
  net=sbx-$n; gw=10.88.$(case $n in coder) echo 1;; infra) echo 2;; admin) echo 3;; web) echo 5;; esac).2
  echo "[$n] network $net, proxy $gw:7890"

  EXIT_IP=$(docker run --rm --network "$net" --entrypoint sh "$IMG" -c \
    "curl -s --max-time 10 -x http://$gw:7890 https://ifconfig.me" 2>/dev/null)
  if [ -z "$EXIT_IP" ]; then
    echo "  · via proxy: no answer (tunnel down → requests blocked, fail-closed)"
  elif [ "$EXIT_IP" != "$HOST_IP" ]; then
    ok "via proxy: exit IP $EXIT_IP (host direct IP $HOST_IP)"
  else
    bad "via proxy: exit IP equals host IP $HOST_IP — traffic is not tunneled!"
  fi

  probe "$net" "curl -s --max-time 5 --noproxy '*' https://1.1.1.1" \
    && bad "direct HTTPS to 1.1.1.1 works — no isolation!" || ok "direct HTTPS blocked (no route)"
  probe "$net" "nslookup -timeout=3 example.com 8.8.8.8" \
    && bad "direct DNS to 8.8.8.8 works — DNS leak!" || ok "direct DNS blocked"
  probe "$net" "curl -s --max-time 3 http://$gw:9090" \
    && bad "dashboard API reachable from agent network!" || ok "dashboard API not reachable from agent"
  probe "$net" "curl -s --max-time 3 http://10.88.0.2:9090" \
    && bad "egress network reachable from agent network!" || ok "egress network not reachable"
done

echo "[vpn] container netns"
docker exec sbx-vpn curl -s --max-time 5 https://1.1.1.1 >/dev/null 2>&1 \
  && bad "vpn container can reach 1.1.1.1 directly — firewall missing!" || ok "direct traffic from vpn netns dropped (only VLESS server allowed)"
docker exec sbx-vpn nft list table inet sbx_killswitch >/dev/null 2>&1 \
  && ok "nft table sbx_killswitch present" || bad "nft table sbx_killswitch missing"

echo "[host] dashboard"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:9090/ui/)
[ "$code" = 200 ] && ok "http://127.0.0.1:9090/ui/ → 200" || bad "dashboard UI → $code"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:9090/connections)
[ "$code" = 401 ] && ok "API without secret → 401" || bad "API without secret → $code"

if docker inspect -f '{{.State.Running}}' sbx-agent-coder 2>/dev/null | grep -q true; then
  echo "[coder] agent container"
  ax() { docker exec sbx-agent-coder sh -c "$*" >/dev/null 2>&1; }
  ax 'grep -rIohE "sk-ant-oat01-[A-Za-z0-9_-]{40,}|gh[po]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]+" /home/agent /etc /tmp 2>/dev/null | grep -qv sbxplaceholder' \
    && bad "real-looking token found inside coder!" || ok "no real tokens inside coder"
  ax 'ls /home/kuchumov/.ssh' && bad "host ~/.ssh visible" || ok "host ~/.ssh not visible"
  ax 'touch /usr/sbx-test' && bad "rootfs writable" || ok "rootfs read-only"
  ax 'curl -s --max-time 5 --noproxy "*" https://1.1.1.1' && bad "coder has a direct route" || ok "coder: direct route blocked"
fi

echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
