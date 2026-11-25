# Radmin VPN on Linux (unofficial)

Radmin VPN only exists for Windows. This project gives you a native Linux window
and tray icon for the Radmin network (26.x.x.x), with two engines behind it:

* **Native (default since 0.6.0)**: [OpenRad](https://github.com/gabrielmf1998/openrad),
  an open source (MIT) Radmin VPN client written in Rust by
  [gringoestrangeiro](https://github.com/gringoestrangeiro/openrad). No VM, no
  Windows, about 6 MB. It creates a `radminvpn0` network interface with your
  26.x address, so Linux games and apps reach the Radmin peers directly.
* **VM**: the **real** Radmin VPN inside a tiny headless Windows VM. Pick it
  with `RADMIN_ENGINE=vm`. Installs from before 0.6.0 stay on it.

![The Linux client](assets/screenshot.png)

## Install

One command, as your normal user (it asks for your password once):

    curl -fsSL https://raw.githubusercontent.com/gabrielmf1998/Radmin-Linux/main/bootstrap.sh | bash

Then open **Radmin VPN** from the app menu (or run `radmin-linux`), click the
power button and use **Network > Join a network** (name and password, like in
Radmin).

* Works on Arch/Manjaro, Debian/Ubuntu/Mint, Fedora and openSUSE, x86_64.
* The native engine downloads only the app and OpenRad (a few MB, checked
  against a pinned sha256). It installs OpenRad root owned in
  `/usr/local/lib/radmin-linux/` and a polkit rule that lets **your user** start
  only `openrad tap-helper` (the short root step that creates `radminvpn0`)
  without a password. Without the rule (`RADMIN_NO_POLKIT_RULE=1`) every
  connect asks for your password.
* **Updates**: Help > Check for app updates (the app also checks once a day
  and tells you). Running the curl command again does the same.

Options go on the bash side of the pipe, e.g. `curl ... | RADMIN_MIRROR=gitlab bash`:

| option | what it does |
|---|---|
| `RADMIN_ENGINE=vm` | the real Radmin in a Windows VM instead of OpenRad (`openrad` switches back) |
| `RADMIN_MIRROR=gitlab` | download from the [GitLab mirror](https://gitlab.com/gabriel17166/radmin-linux) |
| `RADMIN_VERSION=0.6.0` | a specific release instead of the latest |
| `RADMIN_NEW_IMAGE=1` | VM engine: download a fresh VM image even if one is installed |
| `RADMIN_VM_IMAGE=/path/file.qcow2.zst` | VM engine: use an image you already have |

Uninstall: `~/.local/share/radmin-linux/uninstall.sh` (add `--keep-image` to keep the VM).

### Native engine, good to know

* **New identity.** OpenRad cannot read the identity of the official Radmin, so
  the first power on registers a new node (new 26.x address). Join your
  networks again; friends see you as a new member. The identity lives in
  `~/.local/state/openrad` and survives uninstall and reinstall.
* **One engine at a time.** Both use 26.0.0.0/8. Switching to native turns the
  VM link (`radmin-bridge`) off; `RADMIN_ENGINE=vm` turns it back on. Your VM
  image is never touched.
* **Not the official client.** OpenRad is a third party reimplementation of the
  Radmin protocol, not made or supported by Famatech, and the
  [Radmin VPN EULA](https://www.radmin-vpn.com/eula/) forbids reverse
  engineering the official client. Use it knowing that.
* Peers show **UDP** / **TCP** (direct) or **TcpRelay** like in Radmin, with the
  ping time. **System > Diagnostics** checks the service, the Radmin server,
  `radminvpn0` and the polkit rule.

### VM engine

* Needs a CPU with virtualization enabled in the BIOS (VT-x / AMD-V), about
  **12 GB free disk** during the install (9 GB after) and NetworkManager.
* The download (about 3 GB) shows a progress bar, is checked against the release
  checksums and **resumes** if it stops: just run the same command again.
  Nothing big goes to `/tmp`. Updates keep your VM and your Radmin identity.

## Your own Radmin identity (VM engine)

Radmin keeps the node identity (node id, 26.x address, network list) inside
Windows. The VM image of **0.1.0 shipped with the builder's identity**, so
everyone who downloaded it appeared as the same node and saw the same networks.

Since 0.3.0 every install gets its own identity automatically:

* the release image starts with no identity at all;
* each install has its own id (`RADMIN_VM_UUID` in `~/.config/radmin-linux/config.env`);
  when the VM sees a different id (a copied or older image) it resets Radmin to
  a brand new node and the app tells you once;
* **System > Reset Radmin identity** does it by hand at any time.

If you installed 0.1.0: after upgrading, the first boot gives you a new node, so
join your networks again. If you run a Radmin network that someone joined with
a 0.1.0 install, that member may be the shared identity: remove it and change
the network password.

## What's new

See [CHANGELOG.md](CHANGELOG.md). **0.8.0**: the window, menus, dialogs and tray
look and work like Radmin VPN (power button, Join with Private and Gaming tabs,
Create, Properties, Settings). **0.7.0**: native network admin (delete a
network, remove members, make or remove admins), block an IP, relays only and
retry failed peers, right in the app. **0.6.0**: native engine (OpenRad), no VM:
the same window and tray drive it, with join, create, leave, rename and
diagnostics. **0.5.3**: Quit turns the VM off
and closes everything, nothing keeps running after it. **0.5.2**: "Shutting down" while the VM
turns off (it said "Starting"). **0.5.1**: the list stays empty until the
Radmin window has been read (no old peers or networks you left), and the VM
closes the Windows activation dialog at logon, so Radmin is ready about 2
minutes sooner. **0.5.0**: the peer list is read from the real
Radmin window (network names, members, IPs) and online comes from Radmin
itself, so the app shows exactly what Radmin shows, refreshed every 5 s.
**0.4.0**: in-app updates, tray icon with
the state, ping time per peer, settings to start with the session and turn the
VM on with the app, one instance only, lighter refresh. **0.3.0**: own identity
per install, instant window (agent API), VM at about 2% of one core, clean
VNC picture, firewall in the VM, a curl installer that resumes and verifies.

## How it works

Native engine:

    Linux app ──openrad CLI (JSON)──▶ OpenRad service (your user) ──▶ Radmin servers and peers
                                          │ pkexec, polkit rule
                                          └─▶ openrad tap-helper (root, exits) ─▶ radminvpn0 26.x/8

The app (`app/orctl.py`) runs `openrad <command> --json`; the per user OpenRad
service keeps the connection and answers over a private Unix socket in a few
ms. Only the helper runs as root, and only long enough to create the interface.

VM engine:

    Linux app ──HTTP (API) / WMI──▶ Windows VM (1 GB, 1 CPU, headless) ──▶ real Radmin VPN
        │                              │  ICS shares the Radmin adapter
        └── route 26.0.0.0/8 ─── tap ──┘  with the Linux link (NAT)

* **tapradmin** (192.168.137.2) is a private link between Linux and the VM
  (192.168.137.1). Linux routes 26.0.0.0/8 through it; Windows Internet
  Connection Sharing NATs that traffic into the Radmin network.
* **The agent** (`agent/`, runs in the VM as scheduled tasks): gives the install
  its own identity, hardens Windows, keeps ICS applied, never sleeps, self
  repairs every 15 min and serves a small JSON API on the private link only.
* **The app** (`app/`, PySide6): node, status and one section per Radmin
  network with its members. The agent reads the Radmin window through UI
  Automation (names, IPs) and Radmin's own log (who is connected, direct or
  relay); ping adds the response time (older agents fall back to ARP + ping).
  Also connect/disconnect, rename, leave, Radmin updates, tray icon, watchdog.
* **Join / create a network** needs the real Radmin window (it does the
  handshake with the Radmin servers): the app brings it to the front and opens
  it in a VNC viewer.

### Security

* The VM firewall blocks everything coming in except from the Linux link and
  pings. Radmin peers cannot reach Windows services.
* On Linux, the link is in the firewalld zone **block**: the VM cannot open
  connections to your PC (replies to your own connections still pass).
* The VM account is `bench:bench`. It is only reachable from your own PC.

## Limits

* Hosting a server **on Linux** for your Radmin friends is not supported yet:
  the VM NATs Linux, so peers can reach the VM's 26.x address but not your PC.
  Joining servers hosted by others works.
* The image ships Radmin VPN 2.0.4899.9. Radmin 2.1 needs the Windows 7 SHA-2
  update; **System > Check for Radmin update** reports that instead of failing
  silently.
* Starting the VM takes about 10 to 60 s.

## Native ports (no VM)

Other people are writing native Linux Radmin clients, OpenRad (the native
engine above) being the most complete. Copies of those projects, their releases
and a review of OpenRad are kept in [docs/native-ports.md](docs/native-ports.md).

## Troubleshooting

| what | where |
|---|---|
| native: what OpenRad sees | `/usr/local/lib/radmin-linux/openrad status` (or `diagnostics`) |
| native: OpenRad logs | `~/.local/share/OpenRad/logs/` |
| VM: check and repair everything | `~/.local/share/radmin-linux/preflight.sh` |
| install log | `~/.local/share/radmin-linux/install.log` |
| VM console (the Windows screen) | Network > Open the Radmin window |
| VM diagnostics with repair | System > Full diagnostics |
| agent logs (in the VM) | `C:\radmin-agent\*.log` |
| reinstall the VM agent | System > Reinstall the VM agent, or `deploy-agent.sh --force` |

In the app, **System > Settings**: start with the session (in the tray), turn
the VM on when the app opens, notify when the connection drops, check for app
updates automatically (once a day, one request to the GitHub releases API,
nothing else is sent).

Lower level settings live in `~/.config/radmin-linux/config.env` (environment variables win).
Useful ones: `RADMIN_VM_RAM` (MB, default 1024), `RADMIN_VM_SMP` (default 1),
`RADMIN_NET=passt` (alternative VM network, uses more CPU on QEMU 11).

## Building the VM image (maintainers)

    ./build-vm.sh --clean SRC.qcow2      # release image from a working VM (SRC untouched)
    ./build-vm.sh --from-scratch WIN.iso # Windows + Radmin from zero

`--clean` boots a throwaway copy **offline**, installs the current agent, runs
`provision/release-clean.ps1` (no identity, no networks, no browser, logs and
caches gone, neutral computer name, free space zeroed) and writes the split
`.zst` parts plus `SHA256SUMS`. Upload them to a release and put that version
in `IMAGE_VERSION`; app only releases leave `IMAGE_VERSION` alone and carry no
image (`bootstrap.sh` fetches it from the release named there).

## Repository

    agent/       scripts that run inside the VM (identity, harden, api, boot, health, update…)
    app/         the Linux app (PySide6)
    shim/        old WMI state script, fallback for VMs without the API
    provision/   unattended Windows setup and the release cleaner
    vm/          run / stop / view the VM
    bootstrap.sh install.sh uninstall.sh preflight.sh radmin-linux.sh deploy-agent.sh build-vm.sh

Not affiliated with Famatech. Radmin VPN is their product; the VM engine
downloads it from their site. OpenRad is MIT licensed by its authors; its
license is installed next to the binary.
