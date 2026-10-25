#!/bin/sh
# Live tunnel check (needs a real vless.url): host routing to the VLESS server,
# sbx-vpn health, exit IP through the tunnel and from the agents.
# Unlike routing.sh/killswitch.sh this fails while the tunnel is down.
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
bad()  { echo "  ✗ $*"; FAIL=$((FAIL+1)); }
warn() { echo "  ! $*"; }

echo "[tunnel] host"
[ -s secrets/vless.url ] || { bad "secrets/vless.url is empty (bin/sandbox vless)"; exit 1; }
hostport=$(sed -E 's#^vless://[^@]*@([^?#/]+).*#\1#' secrets/vless.url)
server=${hostport%:*}; port=${hostport##*:}
ip=$(getent ahostsv4 "$server" | awk 'NR==1{print $1}')
[ -n "$ip" ] && ok "server resolves: $server → $ip:$port" || bad "cannot resolve $server"
if [ -n "$ip" ]; then
  dev=$(ip route get "$ip" 2>/dev/null | sed -nE 's/.* dev ([^ ]+).*/\1/p')
  case $dev in
    tun*) bad "route to $ip goes via $dev (host VPN in TUN mode wraps the tunnel — exclude this IP or turn TUN off)" ;;
    "")   bad "no route to $ip" ;;
    *)    ok "route to $ip via $dev (direct)" ;;
  esac
  if ip rule | grep -q "not from all iif lo lookup"; then
    warn "a host VPN captures forwarded traffic (ip rule '…iif lo lookup'): Docker egress may go through it"
  fi
  if timeout 5 sh -c "exec 3<>/dev/tcp/$ip/$port" 2>/dev/null || nc -z -w5 "$ip" "$port" 2>/dev/null; then
    ok "TCP $ip:$port reachable from host"
  else
    bad "TCP $ip:$port unreachable from host"
  fi
fi
direct_ip=$(curl -s --max-time 6 https://api.ipify.org)
echo "  · host exit IP: ${direct_ip:-?}"

echo "[tunnel] sbx-vpn"
health=$(docker inspect -f '{{.State.Health.Status}}' sbx-vpn 2>/dev/null)
[ "$health" = healthy ] && ok "container healthy" || bad "container: ${health:-not running}"
code=$(docker exec sbx-vpn curl -s -o /dev/null -w '%{http_code} %{time_total}s' --max-time 10 \
  -x http://127.0.0.1:7890 https://www.gstatic.com/generate_204)
case $code in 204*) ok "generate_204 via tunnel: $code" ;; *) bad "generate_204 via tunnel: ${code:-timeout}" ;; esac
vpn_ip=$(docker exec sbx-vpn curl -s --max-time 10 -x http://127.0.0.1:7890 https://api.ipify.org)
if [ -z "$vpn_ip" ]; then bad "no exit IP through tunnel"
elif [ "$vpn_ip" = "$direct_ip" ]; then bad "exit IP $vpn_ip equals host IP (not tunnelled?)"
else ok "exit IP through tunnel: $vpn_ip"; fi
errs=$(docker logs --since 2m sbx-vpn 2>&1 | grep -E "ERROR|FATAL|WARN" | tail -5)
[ -n "$errs" ] && { warn "sing-box errors (last 2m):"; echo "$errs" | sed 's/^/      /'; }

echo "[tunnel] agents"
for a in opencode coder infra cad assistant; do
  docker ps -q -f "name=^sbx-agent-$a$" | grep -q . || { warn "$a: not running"; continue; }
  got=$(docker exec "sbx-agent-$a" curl -s --max-time 12 https://api.ipify.org 2>/dev/null)
  if [ -n "$got" ] && [ "$got" != "$direct_ip" ]; then ok "$a: exit IP $got"
  else bad "$a: ${got:-no response} (expected tunnel IP)"; fi
done

echo "[tunnel] $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
