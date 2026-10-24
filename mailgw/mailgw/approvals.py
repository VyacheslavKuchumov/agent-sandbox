"""Approval queue (JSON file on a volume) and the actions it can execute."""
import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager

STATE = os.environ.get("MAILGW_STATE", "/var/lib/mailgw/approvals.json")


@contextmanager
def _locked():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            items = json.load(open(STATE))
        except (OSError, ValueError):
            items = []
        box = {"items": items}
        yield box
        tmp = STATE + ".tmp"
        json.dump(box["items"], open(tmp, "w"), indent=2, ensure_ascii=False)
        os.replace(tmp, STATE)


def enqueue(kind, payload, preview, reason):
    item = {"id": uuid.uuid4().hex[:8], "kind": kind, "status": "pending",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason,
            "payload": payload, "preview": preview, "result": None}
    with _locked() as box:
        box["items"].append(item)
    return item


def all_items():
    with _locked() as box:
        return list(box["items"])


def get(item_id):
    return next((i for i in all_items() if i["id"] == item_id), None)


def set_status(item_id, status, result=None):
    with _locked() as box:
        for i in box["items"]:
            if i["id"] == item_id:
                i["status"], i["result"] = status, result
                i["decided"] = time.strftime("%Y-%m-%d %H:%M:%S")
                return i
    return None


def execute(google, item):
    """Run an approved action. Only called from the human-facing CLI."""
    p = item["payload"]
    k = item["kind"]
    if k == "send_draft":
        r = google.send_draft(p["draft_id"])
        return {"message_id": r.get("id")}
    if k == "calendar_create":
        r = google.insert_event(p["event"], notify=True)
        return {"event_id": r.get("id"), "link": r.get("htmlLink")}
    if k == "calendar_update":
        r = google.patch_event(p["event_id"], p["changes"], notify=True)
        return {"event_id": r.get("id")}
    if k == "calendar_delete":
        google.delete_event(p["event_id"], notify=True)
        return {"deleted": p["event_id"]}
    raise ValueError(f"unknown approval kind {k}")
