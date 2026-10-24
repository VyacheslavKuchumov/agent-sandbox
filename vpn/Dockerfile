FROM ghcr.io/sagernet/sing-box:v1.14.3

ARG ZASHBOARD_VERSION=v3.29.1

RUN apk add --no-cache python3 nftables curl iproute2 su-exec \
 && curl -fsSL -o /tmp/ui.zip \
      "https://github.com/Zephyruso/zashboard/releases/download/${ZASHBOARD_VERSION}/dist.zip" \
 && mkdir -p /tmp/ui /opt/zashboard \
 && unzip -q /tmp/ui.zip -d /tmp/ui \
 && cp -r /tmp/ui/dist/. /opt/zashboard/ \
 && rm -rf /tmp/ui /tmp/ui.zip

COPY entrypoint.sh gen-config.py /opt/vpn/
COPY killswitch.nft.tmpl /opt/vpn/

HEALTHCHECK --interval=15s --timeout=10s --start-period=10s --retries=2 \
  CMD curl -sf -o /dev/null --max-time 8 -x http://127.0.0.1:7890 https://www.gstatic.com/generate_204

ENTRYPOINT ["/opt/vpn/entrypoint.sh"]
