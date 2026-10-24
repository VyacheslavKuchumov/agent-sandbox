"""Offline tests for the Drive upload gateway (run inside the mailgw image, see tests/mailgw.sh)."""
import os
import sys
import tempfile

import mailgw.server as server_mod

server_mod.log = lambda *a, **k: None
import mailgw.drive as drive  # noqa: E402
from mailgw.policy import PolicyError  # noqa: E402

drive.log = lambda *a, **k: None
fails = 0


def check(name, cond):
    global fails
    print(("  ✓ " if cond else "  ✗ ") + name)
    fails += not cond


def raises(fn):
    try:
        fn()
    except PolicyError:
        return True
    return False


class FakeDrive:
    def __init__(self):
        self.folders, self.uploads = {}, []

    def drive_folder(self, name, parent=None):
        return self.folders.setdefault((name, parent), f"f{len(self.folders)}")

    def drive_upload(self, parent, name, data, mime):
        self.uploads.append((parent, name, len(data), mime))
        return {"id": "x1", "name": name, "webViewLink": "https://drive/x1"}

    def drive_list(self, parent):
        return [{"name": "a.stl", "mimeType": "model/stl", "size": "3"}]


root = tempfile.mkdtemp()
outside = tempfile.mkdtemp()
open(os.path.join(root, "gear.stl"), "wb").write(b"solid")
open(os.path.join(root, "notes.exe"), "wb").write(b"MZ")
open(os.path.join(outside, "secret.stl"), "wb").write(b"x")
os.symlink(os.path.join(outside, "secret.stl"), os.path.join(root, "link.stl"))

g = FakeDrive()
t = drive.DriveTools(g, models_dir=root)
r = t.drive_upload("gear.stl")
check("upload: model goes into the configured folder", g.uploads[-1][0] == g.folders[(drive.FOLDER, None)] and r["name"] == "gear.stl")
t.drive_upload("gear.stl", subfolder="Gears v2")
check("upload: one-level subfolder created under it", ("Gears v2", g.folders[(drive.FOLDER, None)]) in g.folders)
check("upload: path traversal refused", raises(lambda: t.drive_upload("../" + os.path.relpath(outside, os.path.dirname(root)) + "/secret.stl")))
check("upload: absolute path outside refused", raises(lambda: t.drive_upload(os.path.join(outside, "secret.stl"))))
check("upload: symlink escaping the folder refused", raises(lambda: t.drive_upload("link.stl")))
check("upload: disallowed extension refused", raises(lambda: t.drive_upload("notes.exe")))
check("upload: nested/odd subfolder name refused", raises(lambda: t.drive_upload("gear.stl", subfolder="a/../b")))
check("list: works", t.drive_list()[0]["name"] == "a.stl")
srv = drive.build_server(t, "127.0.0.1", 8000)
import asyncio  # noqa: E402
names = sorted(x.name for x in asyncio.run(srv.list_tools()))
check("mcp: only drive_list and drive_upload exposed", names == ["drive_list", "drive_upload"])
print(f"drive: {'OK' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
