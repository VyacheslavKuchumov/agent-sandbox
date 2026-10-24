FROM python:3.13.15-slim-trixie
COPY requirements.txt /opt/mailgw/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /opt/mailgw/requirements.txt \
 && useradd -u 1000 -U -M -d /nonexistent mailgw \
 && mkdir -p /var/lib/mailgw && chown mailgw:mailgw /var/lib/mailgw
COPY mailgw /opt/mailgw/mailgw
ENV PYTHONPATH=/opt/mailgw PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER mailgw
ENTRYPOINT ["python", "-m", "mailgw.server"]
