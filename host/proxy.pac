// Browser proxy auto-config: everything through the agent-sandbox VPN, falling
// back to DIRECT when the proxy is down (the host has no kill switch on purpose).
// Firefox: Settings → Network → Automatic proxy configuration URL:
//   file:///home/kuchumov/agent-sandbox/host/proxy.pac
function FindProxyForURL(url, host) {
  if (isPlainHostName(host) || host === "localhost" || shExpMatch(host, "*.local") ||
      shExpMatch(host, "127.*") || shExpMatch(host, "10.*") || shExpMatch(host, "192.168.*") ||
      shExpMatch(host, "172.1[6-9].*") || shExpMatch(host, "172.2[0-9].*") || shExpMatch(host, "172.3[01].*"))
    return "DIRECT";
  return "PROXY 127.0.0.1:7891; DIRECT";
}
