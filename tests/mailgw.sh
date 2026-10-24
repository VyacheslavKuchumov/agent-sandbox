#!/bin/sh
# Offline tests of the mail and drive gateways.
cd "$(dirname "$0")/.."
rc=0
for t in mailgw_test drive_test; do
  docker run --rm -v "$PWD/tests/$t.py:/t.py:ro" --entrypoint python agent-sandbox/mailgw:local /t.py || rc=1
done
exit $rc
