"""One-off Google OAuth login (run by `bin/sandbox login assistant google`).

Uses a Desktop OAuth client (client_secret JSON from Google Cloud Console) and
the loopback redirect: after consent the browser lands on http://localhost:8085/…
which shows an error page — copy that URL from the address bar and paste it here.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .google import SCOPES, TOKEN_URL

REDIRECT = "http://localhost:8085/"


def main(client_path, out_path):
    c = json.load(open(client_path))
    c = c.get("installed") or c.get("web") or c
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": c["client_id"], "redirect_uri": REDIRECT, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    print("\n1. Open this URL in your browser and allow access:\n\n" + url + "\n")
    print("2. The browser then fails to open http://localhost:8085/?… — that is expected.")
    pasted = input("3. Paste that full URL from the address bar here: ").strip()
    q = parse_qs(urlsplit(pasted).query)
    if q.get("state", [""])[0] != state:
        sys.exit("state mismatch — start again")
    if "code" not in q:
        sys.exit(f"no code in URL: {q.get('error', ['?'])[0]}")
    r = httpx.post(TOKEN_URL, data={
        "client_id": c["client_id"], "client_secret": c.get("client_secret", ""), "code": q["code"][0],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": REDIRECT}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"token exchange failed: HTTP {r.status_code} {r.text[:200]}")
    tok = r.json()
    if "refresh_token" not in tok:
        sys.exit("no refresh_token returned (revoke the app's access in your Google account and retry)")
    granted = set(tok.get("scope", "").split())
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                   "refresh_token": tok["refresh_token"], "scopes": sorted(granted)}, f, indent=2)
    missing = set(SCOPES) - granted
    print("saved." + (f" WARNING: scopes not granted: {sorted(missing)}" if missing else ""))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
