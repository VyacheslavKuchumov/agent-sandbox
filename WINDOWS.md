# Deploying agent-sandbox on Windows (instructions for an AI agent)

You are an AI agent asked to deploy this repository on a Windows machine. Read
`ARCHITECTURE.md` and `LIMITATIONS.md` first. The system was built and tested on
Arch Linux. On Windows it runs **inside WSL2 with Docker Engine installed in the
WSL distro**. Follow the steps in order and verify each one. Never weaken an
isolation property to make something "work". If a step fails, stop and report it.

## 0. Decide the runtime (do not skip)

| Option | Use it? | Why |
|---|---|---|
| **WSL2 + Docker Engine inside the distro** | ✅ yes | Same Linux semantics as the original host: bind mounts, uids, `nftables` in the `vpn` container, `pid: host` and `/` for agent-admin refer to the WSL distro |
| Docker Desktop (WSL2 backend) | ⚠️ only if the user insists | Containers run in Docker Desktop's own VM. agent-admin would watch that VM, not the user's system. Bind-mount uid/permission behaviour differs, and some tests will not mean what they claim |
| Docker Desktop (Hyper-V / Windows containers) | ❌ no | Not supported |

## 1. WSL2 base

In an **elevated PowerShell** (ask the user to run these; they change the system):
```powershell
wsl --install -d Debian        # or Ubuntu; any systemd-capable distro
wsl --update                   # recent WSL kernel (nf_tables, wireguard)
```
`%UserProfile%\.wslconfig` (agents have memory limits up to 8 GB each):
```ini
[wsl2]
memory=16GB
processors=8
localhostForwarding=true
```
Inside the distro, enable systemd: in `/etc/wsl.conf`
```ini
[boot]
systemd=true
```
then run `wsl --shutdown` from PowerShell and reopen the distro.

## 2. Docker Engine in the distro

Install Docker Engine, the compose plugin and buildx from Docker's apt repository
(docs.docker.com/engine/install/debian). The Debian `docker.io` package also works,
but **buildx must be present** (LIMITATIONS 3.6 / 5.4a):
```sh
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
sudo usermod -aG docker "$USER"   # then log out of the distro and back in
docker run --rm hello-world
```
Check that the kernel can do what the `vpn` container needs:
```sh
docker run --rm --cap-add NET_ADMIN alpine sh -c 'apk add -q nftables && nft add table inet t && echo NFT_OK'
```
If this does not print `NFT_OK`, stop. The kill switch would not load (the `vpn`
container refuses to start without it, which is correct). Fix the kernel
(`wsl --update`, or a custom WSL kernel with `CONFIG_NF_TABLES`) and do not
remove the firewall step.

## 3. Get the repository (inside WSL, not on C:)

```sh
git config --global core.autocrlf false        # CRLF breaks the shell scripts
cd ~ && git clone https://github.com/<user>/agent-sandbox.git
cd agent-sandbox
```
Keep it on the Linux filesystem (`/home/<user>/…`), **not** under `/mnt/c`:
drvfs has no real uids and permissions, so `chmod 600` on `secrets/` would be
meaningless there.

## 4. Adapt host-specific values

- `.env`: set `PROJECTS_DIR`, `SANDBOX_DIR`, `MODELS_DIR` to paths inside WSL
  (e.g. `/home/<user>/Projects`). Projects that live on Windows can be *copied*
  into WSL. Mounting `/mnt/c/...` into agents is possible but slow, and
  everything there looks writable to everyone.
- **uid**: `compose.yaml` assumes the WSL user is uid/gid `1000`. Check `id -u`.
  If it differs, replace `1000:1000` in `compose.yaml` and `agents/*/Dockerfile`
  consistently, or create the WSL user with uid 1000.
- Run `bin/sandbox init`. It refreshes `HOST_JOURNAL_GID` / `HOST_ADM_GID` /
  `HOST_USER_GID` in `.env` from the distro's groups.
- **agent-admin and the Windows drives (important)**: admin mounts the distro's `/`
  read-only, and that includes `/mnt/c`. On drvfs every Windows file appears owned
  by uid 1000 with open permissions, and admin is in group 1000, so it could read
  the whole C: drive. Add a tmpfs over it in the `admin` service:
  ```yaml
      tmpfs:
        - /tmp:size=512m
        - /host/mnt:size=1m,mode=0755
  ```
  Verify: `docker exec sbx-agent-admin ls /host/mnt` must be empty.

## 5. Bring it up and verify

```sh
bin/sandbox vless                    # the user pastes the vless:// key
bin/sandbox up coder infra admin monitor
bin/sandbox test                     # all suites must pass, no ✗
```
Then follow REPORT.md "Your steps": logins via `bin/sandbox login …`. The browser
runs on Windows. Login links and the Google redirect URL paste work the same way.

Dashboards from the Windows browser (WSL forwards localhost TCP):
`http://127.0.0.1:9090/ui/` (connections), `http://127.0.0.1:3300/` (Grafana).

## 6. herdr

Install herdr's Linux build **inside WSL** and use it from Windows Terminal.
`bin/agent …` works unchanged.

## 7. What does NOT carry over to Windows

| Feature | On Windows | What to do |
|---|---|---|
| Host full-tunnel VPN via NetworkManager (`bin/sandbox host-vpn`) | ❌ no NetworkManager. A WireGuard-for-Windows tunnel would also capture WSL's own traffic, including the `vpn` container's VLESS connection, and loop | Do not set up a full tunnel. Use the browser proxy: PAC `host/proxy.pac` → `127.0.0.1:7891` (TCP localhost forwarding works). Tell the user that only proxy-aware apps go through the VPN |
| `sandbox-host-watch` | ❌ not needed without the NM profile | skip |
| OpenSnitch (host app firewall) | ❌ Linux only | Suggest a Windows per-app firewall/monitor (e.g. Portmaster, simplewall). It is not wired into Grafana. Say so in your report |
| agent-admin | ⚠️ observes the **WSL distro**, not Windows | Make sure the user knows. Never mount Windows system paths into it |
| Kill switch for agents | ✅ unchanged (it lives inside Docker networks) | `bin/sandbox test` proves it |

## 8. Report back

Write `REPORT-WINDOWS.md` covering:
- every value you changed (uids, paths, the admin tmpfs);
- the `bin/sandbox test` output;
- which features from section 7 are unavailable.

Do not commit `secrets/`.
