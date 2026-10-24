"""Human-facing approval CLI (bin/sandbox approvals …). Never exposed to the agent.

  python -m mailgw.approve list
  python -m mailgw.approve show <id>
  python -m mailgw.approve approve <id>
  python -m mailgw.approve deny <id>
"""
import os
import sys

from . import approvals
from .server import log


def main(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        items = [i for i in approvals.all_items() if i["status"] == "pending" or "-a" in argv]
        if not items:
            print("no pending requests")
        for i in items:
            first = i["preview"].splitlines()[0]
            print(f"{i['id']}  {i['status']:8} {i['created']}  {first}  ({i['kind']})")
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 1
    item = approvals.get(argv[1])
    if not item:
        print("unknown id")
        return 1
    if cmd == "show":
        print(f"id: {item['id']}   kind: {item['kind']}   status: {item['status']}   created: {item['created']}")
        print(f"agent's reason: {item['reason'] or '-'}\n" + "-" * 70)
        print(item["preview"])
        return 0
    if item["status"] != "pending":
        print(f"already {item['status']}")
        return 1
    if cmd == "deny":
        approvals.set_status(item["id"], "denied")
        log("decision", item["kind"], f"{item['id']} denied")
        print("denied")
        return 0
    if cmd == "approve":
        from .google import Google
        g = Google(os.environ.get("MAILGW_CREDS", "/run/google/google.json"))
        try:
            result = approvals.execute(g, item)
        except Exception as e:
            approvals.set_status(item["id"], "failed", str(e))
            log("decision", item["kind"], f"{item['id']} approved, failed: {e}", ok=False)
            print(f"failed: {e}")
            return 1
        approvals.set_status(item["id"], "done", result)
        log("decision", item["kind"], f"{item['id']} approved → {result}")
        print(f"done: {result}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
