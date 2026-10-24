#!/usr/bin/env python3
"""Build sing-box config from a vless:// URL.

Reads:
  /run/secrets/vless_url         — vless://uuid@host:port?params#name (first non-comment line)
  /run/secrets/dashboard_secret  — Clash API secret
  env AGENT_INBOUNDS             — "coder=10.88.1.2,infra=10.88.2.2,admin=10.88.3.2"
  env CONTROLLER                 — "10.88.0.2:9090"
Writes:
  $RUN_DIR/config.json   — sing-box config (0600)
  $RUN_DIR/server.env    — SERVER_IP / SERVER_PORT for the kill switch firewall
"""
import ipaddress
import json
import os
import socket
import sys
from urllib.parse import parse_qs, unquote, urlsplit

RUN_DIR = os.environ.get("RUN_DIR", "/run/sing-box")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "7890"))


def die(msg):
    print(f"gen-config: {msg}", file=sys.stderr)
    sys.exit(1)


def read_secret(name):
    path = f"/run/secrets/{name}"
    try:
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    except OSError as e:
        die(f"cannot read {path}: {e}")
    if not lines:
        die(f"{path} is empty — put your key there (see secrets/vless.url.example)")
    return lines[0]


def resolve_ipv4(host):
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror as e:
        die(f"cannot resolve VLESS server {host!r}: {e}")
    return infos[0][4][0]


def first(q, key, default=""):
    return q.get(key, [default])[0]


def vless_outbound(url):
    u = urlsplit(url)
    if u.scheme != "vless":
        die(f"expected vless:// URL, got {u.scheme!r}://")
    if not u.username or not u.hostname or not u.port:
        die("URL must look like vless://UUID@HOST:PORT?...")
    q = parse_qs(u.query)
    host = u.hostname
    server_ip = resolve_ipv4(host)

    out = {
        "type": "vless",
        "tag": "proxy",
        "server": server_ip,
        "server_port": u.port,
        "uuid": unquote(u.username),
        "packet_encoding": "xudp",
    }
    flow = first(q, "flow")
    if flow:
        out["flow"] = flow

    security = first(q, "security", "none")
    if security in ("tls", "reality"):
        tls = {"enabled": True, "server_name": first(q, "sni") or first(q, "host") or host}
        alpn = first(q, "alpn")
        if alpn:
            tls["alpn"] = unquote(alpn).split(",")
        if first(q, "allowInsecure") in ("1", "true"):
            tls["insecure"] = True
        fp = first(q, "fp")
        if fp or security == "reality":
            tls["utls"] = {"enabled": True, "fingerprint": fp or "chrome"}
        if security == "reality":
            pbk = first(q, "pbk")
            if not pbk:
                die("security=reality requires pbk (public key)")
            tls["reality"] = {"enabled": True, "public_key": pbk, "short_id": first(q, "sid")}
        out["tls"] = tls
    elif security != "none":
        die(f"unsupported security={security!r}")

    net = first(q, "type", "tcp")
    path = unquote(first(q, "path", "/"))
    thost = first(q, "host")
    if net == "tcp":
        if first(q, "headerType") == "http":
            die("tcp+http header obfuscation is not supported")
    elif net == "ws":
        t = {"type": "ws", "path": path}
        if "?ed=" in path:
            path, ed = path.split("?ed=", 1)
            t["path"] = path
            t["max_early_data"] = int(ed)
            t["early_data_header_name"] = "Sec-WebSocket-Protocol"
        if thost:
            t["headers"] = {"Host": thost}
        out["transport"] = t
    elif net == "grpc":
        out["transport"] = {"type": "grpc", "service_name": unquote(first(q, "serviceName"))}
    elif net == "httpupgrade":
        t = {"type": "httpupgrade", "path": path}
        if thost:
            t["host"] = thost
        out["transport"] = t
    elif net in ("http", "h2"):
        t = {"type": "http", "path": path}
        if thost:
            t["host"] = thost.split(",")
        out["transport"] = t
    elif net in ("xhttp", "splithttp"):
        die(f"type={net} is Xray-only and not supported by sing-box (LIMITATIONS.md 1.7)")
    else:
        die(f"unsupported transport type={net!r}")
    return out, server_ip, u.port


def only_for(inbounds, allowed):
    """Reject traffic from `inbounds` that does not match `allowed`.

    A logical AND is required: `invert` on a plain rule would negate the inbound
    match too and reject every other agent's traffic.
    """
    return {"type": "logical", "mode": "and", "action": "reject",
            "rules": [{"inbound": inbounds}, {**allowed, "invert": True}]}


def restriction_rules():
    rules = []
    # WEB_ONLY_PORTS=web → in-web may only reach ports 80/443.
    tags = [f"in-{n}" for n in filter(None, os.environ.get("WEB_ONLY_PORTS", "").split(","))]
    if tags:
        rules.append(only_for(tags, {"port": [80, 443]}))
    # DOMAIN_ALLOW="mailgw=googleapis.com,google.com;keys=api.anthropic.com" → domain allow-lists.
    for item in filter(None, os.environ.get("DOMAIN_ALLOW", "").split(";")):
        name, domains = item.split("=", 1)
        rules.append(only_for([f"in-{name}"], {"domain_suffix": domains.split(",")}))
    return rules


def host_endpoints():
    """Optional WireGuard endpoint for the host (bin/sandbox host-vpn install)."""
    try:
        wg = json.load(open("/run/secrets/host_wg"))
    except (OSError, ValueError):
        return []
    if not wg.get("server_private"):
        return []
    return [{
        "type": "wireguard",
        "tag": "in-host-wg",
        "system": False,
        "address": ["10.99.0.1/24"],
        "private_key": wg["server_private"],
        "listen_port": int(os.environ.get("HOST_WG_PORT", "51820")),
        "peers": [{"public_key": wg["client_public"], "allowed_ips": ["10.99.0.2/32"]}],
    }]


def main():
    outbound, server_ip, server_port = vless_outbound(read_secret("vless_url"))
    dash_secret = read_secret("dashboard_secret")

    inbounds = [{"type": "mixed", "tag": "in-local", "listen": "127.0.0.1", "listen_port": PROXY_PORT}]
    for item in filter(None, os.environ.get("AGENT_INBOUNDS", "").split(",")):
        name, ip = item.split("=")
        ipaddress.IPv4Address(ip)
        inbounds.append({"type": "mixed", "tag": f"in-{name}", "listen": ip, "listen_port": PROXY_PORT})
    host_proxy = os.environ.get("HOST_PROXY")  # e.g. 10.88.0.2:7891, published on 127.0.0.1
    if host_proxy:
        ip, port = host_proxy.rsplit(":", 1)
        inbounds.append({"type": "mixed", "tag": "in-host", "listen": ip, "listen_port": int(port)})
    endpoints = host_endpoints()

    config = {
        "log": {"level": os.environ.get("LOG_LEVEL", "info"), "timestamp": True},
        "dns": {
            # DoH strictly inside the tunnel; there is no direct outbound at all.
            "servers": [{"type": "https", "tag": "doh", "server": "1.1.1.1", "detour": "proxy"}],
            "final": "doh",
            "strategy": "ipv4_only",
        },
        "inbounds": inbounds,
        **({"endpoints": endpoints} if endpoints else {}),
        "outbounds": [outbound],
        "route": {
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
                {"ip_version": 6, "action": "reject"},
                *restriction_rules(),
            ],
            "final": "proxy",
        },
        "experimental": {
            "clash_api": {
                "external_controller": os.environ.get("CONTROLLER", "0.0.0.0:9090"),
                "external_ui": "/opt/zashboard",
                "secret": dash_secret,
            }
        },
    }

    os.makedirs(RUN_DIR, exist_ok=True)
    cfg_path = os.path.join(RUN_DIR, "config.json")
    fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)
    with open(os.path.join(RUN_DIR, "server.env"), "w") as f:
        f.write(f"SERVER_IP={server_ip}\nSERVER_PORT={int(server_port)}\n")

    t = outbound.get("transport", {}).get("type", "tcp")
    sec = "reality" if outbound.get("tls", {}).get("reality") else ("tls" if "tls" in outbound else "none")
    print(f"gen-config: vless {server_ip}:{server_port} transport={t} security={sec}"
          f"{' host-proxy=' + host_proxy if host_proxy else ''}{' host-wg=on' if endpoints else ''}")


if __name__ == "__main__":
    main()
