"""Codex (ChatGPT subscription) token faking, shared by the broker and bin/sandbox.

The agent gets an auth.json whose tokens are *derived fakes*: JWTs keep their
header and payload (codex reads claims such as plan and account id locally) but
carry a bogus signature; opaque tokens become a hash. Only the broker holds the
real auth.json and maps fakes back to real values on the way out.

CLI:  python3 codexauth.py fake < real-auth.json > agent-auth.json
"""
import hashlib
import json
import sys

TOKEN_FIELDS = ("id_token", "access_token", "refresh_token")
FAKE_SIG = "sbxfake"


def fake(real):
    if not real:
        return real
    h = hashlib.sha256(real.encode()).hexdigest()[:24]
    parts = real.split(".")
    if len(parts) == 3:
        return f"{parts[0]}.{parts[1]}.{FAKE_SIG}{h}"
    return f"sbxfake-{h}"


def fake_auth(auth):
    out = json.loads(json.dumps(auth))
    tokens = out.get("tokens") or {}
    for k in TOKEN_FIELDS:
        if tokens.get(k):
            tokens[k] = fake(tokens[k])
    if out.get("OPENAI_API_KEY"):
        out["OPENAI_API_KEY"] = fake(out["OPENAI_API_KEY"])
    return out


def fake_to_real(auth):
    """{fake: real} for every secret in a real auth.json."""
    m = {}
    tokens = auth.get("tokens") or {}
    for k in TOKEN_FIELDS:
        if tokens.get(k):
            m[fake(tokens[k])] = tokens[k]
    if auth.get("OPENAI_API_KEY"):
        m[fake(auth["OPENAI_API_KEY"])] = auth["OPENAI_API_KEY"]
    return m


if __name__ == "__main__":
    if sys.argv[1:] != ["fake"]:
        sys.exit("usage: codexauth.py fake < auth.json")
    json.dump(fake_auth(json.load(sys.stdin)), sys.stdout, indent=2)
