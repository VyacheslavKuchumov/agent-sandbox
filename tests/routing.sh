#!/bin/sh
# Per-inbound routing policy in sing-box, checked from its log: a request either
# reaches the VLESS outbound ("outbound connection") or is rejected before it.
# Works with a dead tunnel too (we only look at the routing decision).
cd "$(dirname "$0")/.."
IMG=agent-sandbox/vpn:local
PASS=0; FAIL=0
decision() {  # net proxy url → "out" | "rejected"
  since=$(date -u +%Y-%m-%dT%H:%M:%S)
  docker run --rm --network "$1" --entrypoint curl "$IMG" -s -o /dev/null --max-time 4 -x "$2" "$3" >/dev/null 2>&1
  sleep 1
  host=$(echo "$3" | sed -E 's#^[a-z]+://([^/:]+).*#\1#')
  if docker logs --since "$since" sbx-vpn 2>&1 | grep -q "outbound connection to $host"; then echo out; else echo rejected; fi
}
expect() {  # name expected net proxy url
  got=$(decision "$3" "$4" "$5")
  if [ "$got" = "$2" ]; then echo "  ✓ $1 → $got"; PASS=$((PASS+1)); else echo "  ✗ $1 → $got (expected $2)"; FAIL=$((FAIL+1)); fi
}
echo "[routing] sing-box per-agent policy"
expect "coder: any https"              out      sbx-coder  http://10.88.1.2:7890 https://example.com/
expect "coder: non-web port allowed"   out      sbx-coder  http://10.88.1.2:7890 http://www.example.com:8080/
expect "web: https"                    out      sbx-web    http://10.88.5.2:7890 https://example.org/
expect "web: port 8080 rejected"       rejected sbx-web    http://10.88.5.2:7890 http://iana.org:8080/
expect "mailgw: googleapis allowed"    out      sbx-mailgw http://10.88.6.2:7890 https://gmail.googleapis.com/
expect "mailgw: other domain rejected" rejected sbx-mailgw http://10.88.6.2:7890 https://example.net/
expect "keys: anthropic allowed"       out      sbx-keys   http://10.88.9.2:7890 https://api.anthropic.com/
expect "keys: other domain rejected"   rejected sbx-keys   http://10.88.9.2:7890 https://example.edu/
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ]
