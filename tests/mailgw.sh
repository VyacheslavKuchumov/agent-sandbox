#!/bin/sh
# Offline tests of the mail gateway policy.
cd "$(dirname "$0")/.."
exec docker run --rm -v "$PWD/tests/mailgw_test.py:/t.py:ro" --entrypoint python agent-sandbox/mailgw:local /t.py
