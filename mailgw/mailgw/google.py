"""Minimal Gmail + Calendar REST client. Tokens never leave this process."""
import base64
import json
import time
from email.message import EmailMessage

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/calendar.events"]


class GoogleError(Exception):
    pass


class Google:
    def __init__(self, creds_path, http=None):
        self.creds = json.load(open(creds_path))
        self.http = http or httpx.Client(timeout=30)  # honours HTTPS_PROXY (→ vpn)
        self._token, self._exp = None, 0.0
        self._labels = None

    # --- auth ---
    def _access_token(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = self.http.post(TOKEN_URL, data={
            "client_id": self.creds["client_id"],
            "client_secret": self.creds["client_secret"],
            "refresh_token": self.creds["refresh_token"],
            "grant_type": "refresh_token",
        })
        if r.status_code != 200:
            raise GoogleError(f"token refresh failed: HTTP {r.status_code}")
        d = r.json()
        self._token, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self._token

    def _call(self, method, url, **kw):
        r = self.http.request(method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, **kw)
        if r.status_code >= 400:
            raise GoogleError(f"{method} {url.split('?')[0]} → HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # --- gmail ---
    def search(self, query, max_results):
        ids = self._call("GET", f"{GMAIL}/messages", params={"q": query, "maxResults": max_results})
        out = []
        for m in ids.get("messages", []):
            meta = self._call("GET", f"{GMAIL}/messages/{m['id']}", params=[
                ("format", "metadata"), *[("metadataHeaders", h) for h in ("From", "To", "Subject", "Date")]])
            out.append(meta)
        return out

    def message(self, msg_id):
        return self._call("GET", f"{GMAIL}/messages/{msg_id}", params={"format": "full"})

    def labels(self):
        if self._labels is None:
            self._labels = self._call("GET", f"{GMAIL}/labels").get("labels", [])
        return self._labels

    def modify(self, msg_id, add, remove):
        return self._call("POST", f"{GMAIL}/messages/{msg_id}/modify",
                          json={"addLabelIds": add, "removeLabelIds": remove})

    def create_draft(self, to, subject, body, cc=(), reply_to=None):
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        thread_id = None
        if reply_to:
            orig = self._call("GET", f"{GMAIL}/messages/{reply_to}", params=[
                ("format", "metadata"), ("metadataHeaders", "Message-ID"), ("metadataHeaders", "References")])
            hdr = {h["name"].lower(): h["value"] for h in orig.get("payload", {}).get("headers", [])}
            if hdr.get("message-id"):
                msg["In-Reply-To"] = hdr["message-id"]
                msg["References"] = (hdr.get("references", "") + " " + hdr["message-id"]).strip()
            thread_id = orig.get("threadId")
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload = {"message": {"raw": raw, **({"threadId": thread_id} if thread_id else {})}}
        return self._call("POST", f"{GMAIL}/drafts", json=payload)

    def draft(self, draft_id):
        return self._call("GET", f"{GMAIL}/drafts/{draft_id}", params={"format": "full"})

    def send_draft(self, draft_id):
        return self._call("POST", f"{GMAIL}/drafts/send", json={"id": draft_id})

    # --- calendar ---
    def events(self, time_min, time_max, max_results):
        return self._call("GET", f"{CAL}/events", params={
            "timeMin": time_min, "timeMax": time_max, "maxResults": max_results,
            "singleEvents": "true", "orderBy": "startTime"}).get("items", [])

    def event(self, event_id):
        return self._call("GET", f"{CAL}/events/{event_id}")

    def insert_event(self, body, notify):
        return self._call("POST", f"{CAL}/events", params={"sendUpdates": "all" if notify else "none"}, json=body)

    def patch_event(self, event_id, body, notify):
        return self._call("PATCH", f"{CAL}/events/{event_id}", params={"sendUpdates": "all" if notify else "none"}, json=body)

    def delete_event(self, event_id, notify):
        return self._call("DELETE", f"{CAL}/events/{event_id}", params={"sendUpdates": "all" if notify else "none"})
