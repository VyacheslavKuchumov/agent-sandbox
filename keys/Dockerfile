FROM mitmproxy/mitmproxy:12.2.3

COPY policy.json inject.py start.py codexauth.py /opt/keys/
RUN chmod 0755 /opt/keys/start.py

USER mitmproxy
ENTRYPOINT ["python3", "/opt/keys/start.py"]
