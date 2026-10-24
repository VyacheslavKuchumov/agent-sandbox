"""MCP server for the cad agent: upload finished models to one Google Drive folder.

Only files under MODELS_DIR (mounted read-only) can be uploaded, only into the
folder "$DRIVE_FOLDER" (and its subfolders), with a model/doc extension and a
size cap. The token has the drive.file scope, so nothing else on the Drive is
visible. No delete, no download.

Run: python -m mailgw.drive   (streamable HTTP on $MAILGW_LISTEN)
"""
import mimetypes
import os
import re
import sys
import time

from .policy import PolicyError, RateLimiter
from .server import log

MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
FOLDER = os.environ.get("DRIVE_FOLDER", "Agent Sandbox — 3D models")
MAX_BYTES = int(os.environ.get("DRIVE_MAX_MB", "200")) * 1024 * 1024
EXTENSIONS = {".fcstd", ".blend", ".stl", ".step", ".stp", ".iges", ".igs", ".obj", ".3mf", ".ply", ".glb",
              ".gltf", ".fbx", ".dxf", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".txt", ".md", ".gcode"}
RateLimiter.LIMITS.setdefault("upload", (30, 3600))


class DriveTools:
    def __init__(self, google, limiter=None, models_dir=MODELS_DIR):
        self.g = google
        self.rl = limiter or RateLimiter()
        self.root = os.path.realpath(models_dir)
        self._folder = None

    def _folder_id(self, subfolder):
        if self._folder is None:
            self._folder = self.g.drive_folder(FOLDER)
        if not subfolder:
            return self._folder
        if not re.fullmatch(r"[\w .()\-]{1,80}", subfolder):
            raise PolicyError("subfolder: letters, digits, space, . _ - ( ) only, one level")
        return self.g.drive_folder(subfolder, self._folder)

    def _local(self, path):
        full = os.path.realpath(os.path.join(self.root, path))
        if os.path.commonpath([full, self.root]) != self.root:
            raise PolicyError(f"only files under {MODELS_DIR} can be uploaded")
        if not os.path.isfile(full):
            raise PolicyError(f"no such file: {path}")
        if os.path.splitext(full)[1].lower() not in EXTENSIONS:
            raise PolicyError(f"extension not allowed; allowed: {sorted(EXTENSIONS)}")
        if os.path.getsize(full) > MAX_BYTES:
            raise PolicyError(f"file larger than {MAX_BYTES // 1024 // 1024} MB")
        return full

    def drive_upload(self, path: str, subfolder: str | None = None) -> dict:
        self.rl.check("upload")
        full = self._local(path)
        parent = self._folder_id(subfolder)
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            r = self.g.drive_upload(parent, os.path.basename(full), f.read(), mime)
        where = f"{FOLDER}/{subfolder}" if subfolder else FOLDER
        log("tool", "drive_upload", f"{path} → {where} ({r.get('id')})")
        return {"file_id": r.get("id"), "name": r.get("name"), "folder": where, "link": r.get("webViewLink")}

    def drive_list(self, subfolder: str | None = None) -> list:
        self.rl.check("read")
        files = self.g.drive_list(self._folder_id(subfolder))
        return [{"name": f["name"], "size": f.get("size"), "modified": f.get("modifiedTime"),
                 "folder": f["mimeType"].endswith("folder"), "link": f.get("webViewLink")} for f in files]


DESCRIPTIONS = {
    "drive_upload": f"Upload a file from {MODELS_DIR} (path relative to it) to the user's Google Drive folder "
                    f"'{FOLDER}' (optionally into one subfolder). Same name → new content replaces the old.",
    "drive_list": f"List files in the Drive folder '{FOLDER}' (or one subfolder).",
}


def build_server(tools, host, port):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP("drive", host=host, port=port,
                  instructions=f"Upload finished 3D models from {MODELS_DIR} to the user's Google Drive folder.",
                  transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=True,
                      allowed_hosts=[f"{os.environ.get('MAILGW_PUBLIC_HOST', '10.88.8.4')}:{port}"]))
    for name, desc in DESCRIPTIONS.items():
        mcp.add_tool(getattr(tools, name), name=name, description=desc)
    return mcp


def main():
    from .google import Google
    creds = os.environ.get("MAILGW_CREDS", "/run/google/google-drive.json")
    while not os.path.exists(creds):
        print(f"drivegw: waiting for Google Drive login ({creds}) — run: bin/sandbox login cad google",
              file=sys.stderr, flush=True)
        time.sleep(30)
    host, port = os.environ.get("MAILGW_LISTEN", "0.0.0.0:8000").rsplit(":", 1)
    server = build_server(DriveTools(Google(creds)), host, int(port))
    log("start", "-", f"drive gateway on {host}:{port}/mcp, folder {FOLDER!r}")
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
