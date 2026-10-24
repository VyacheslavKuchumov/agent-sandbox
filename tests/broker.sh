#!/bin/sh
# Offline unit tests of the credential broker addon.
cd "$(dirname "$0")/.."
exec docker run --rm -v "$PWD/tests/broker_test.py:/t.py:ro" --entrypoint python3 agent-sandbox/keys:local /t.py
