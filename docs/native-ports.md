# Native Linux ports of Radmin VPN (archive)

Radmin-Linux runs the real Radmin VPN inside a small Windows 7 VM. Other people
are writing **native** Linux clients that speak the Radmin protocol directly,
with no VM. This page keeps a copy of those projects so the work is never lost
and never has to start from zero.

None of these projects are ours and none are affiliated with Famatech. Each copy
keeps its full git history, its authors and its license. The release files are
copied unchanged and, where upstream published sha256 digests, checked against
them.

Note that the [Radmin VPN EULA](https://www.radmin-vpn.com/eula/) forbids
reverse engineering the official client. These projects were written and
published by their own authors; read their licenses and notes before using them.

## The copies

| Project | What it is | License | Upstream | GitHub copy | GitLab copy |
|---|---|---|---|---|---|
| **OpenRad** | Full Rust reimplementation: Linux TAP interface, desktop app, CLI, TCP, UDP and relay peers, Android app, self hosted server | MIT | [gringoestrangeiro/openrad](https://github.com/gringoestrangeiro/openrad) | [gabrielmf1998/openrad](https://github.com/gabrielmf1998/openrad) | [gabriel17166/openrad](https://gitlab.com/gabriel17166/openrad) |
| radmin-linux-rs | Early Rust client, protocol research notes | MIT | [Pedrodroks34/radmin-linux-rs](https://github.com/Pedrodroks34/radmin-linux-rs) | [gabrielmf1998/radmin-linux-rs](https://github.com/gabrielmf1998/radmin-linux-rs) | [gabriel17166/radmin-linux-rs](https://gitlab.com/gabriel17166/radmin-linux-rs) |
| radmin-vpn-linux | The official client under Wine with a custom driver and TAP bridge. The branch `marcelo1565/main` holds the Marcelo1565 fork | GPL-3.0 | [baptisterajaut/radmin-vpn-linux](https://github.com/baptisterajaut/radmin-vpn-linux) | [gabrielmf1998/radmin-vpn-linux](https://github.com/gabrielmf1998/radmin-vpn-linux) | [gabriel17166/radmin-vpn-linux](https://gitlab.com/gabriel17166/radmin-vpn-linux) |
| radmin-on-linux | Radmin LAN/VPN on new and old Linux versions | GPL-3.0 | [Lackind/radmin-on-linux](https://github.com/Lackind/radmin-on-linux) | [gabrielmf1998/radmin-on-linux](https://github.com/gabrielmf1998/radmin-on-linux) | [gabriel17166/radmin-on-linux](https://gitlab.com/gabriel17166/radmin-on-linux) |
| linmin-vpn | Tray front end (Rust + Slint) for the Radmin control service | GPL-3.0 | [LinCastr/linmin-vpn](https://github.com/LinCastr/linmin-vpn) | [gabrielmf1998/linmin-vpn](https://github.com/gabrielmf1998/linmin-vpn) | [gabriel17166/linmin-vpn](https://gitlab.com/gabriel17166/linmin-vpn) |

Every upstream release is copied to the **Releases** page of both copies, with a
note on top linking the original release. Copied on 2026-11-24 (OpenRad up to
v2.4.5).
GitLab release uploads stop near 100 MB, so the two AppImages above that size
(radmin-on-linux v0.1.0 and radmin-vpn-linux v0.2.0) are in each GitLab copy's
**Package Registry** and linked from the release.

`RhuanXDK/radmin-vpn-linux-rs`, an older fork of radmin-linux-rs, was already
deleted upstream and could not be copied.

## OpenRad review (v2.4.5, 2026-11-24)

What was checked before copying, without running anything on the real network:

* **Network.** The client only talks to the Radmin bootstrap server
  `148.113.190.78:17301` (in `src/config.rs`, overridable with `--host`), to the
  servers and peers that server hands out, and to
  `api.github.com/repos/gringoestrangeiro/openrad/releases/latest` for the
  update notice (through the system `curl`, HTTPS only). No other hosts or
  telemetry were found in `src/` and `desktop/src/`.
* **Root.** Only the short `openrad tap-helper` runs as root, through `sudo -n`
  or `pkexec`. It refuses addresses outside 26.0.0.0/8, opens `/dev/net/tun`,
  creates the `openrad` TAP interface (not persistent), sets the 26.x address and
  two routes with `/usr/bin/ip`, hands the descriptor back over a Unix socket and
  exits. Closing the app removes the interface. An existing interface is never
  touched.
* **Updates.** The update check only shows a notice with a link; it downloads and
  runs nothing.
* **Build.** `desktop/build.rs` only embeds an icon on Windows and does nothing on
  Linux.
* **Tests.** Built offline in a sandbox with Arch's Rust 1.100
  (`cargo build --release --locked --offline --workspace`, about 3 minutes).
  `cargo test` passed 407 tests. Two failed only because the sandbox has no
  network route (they need a non loopback local address).

Not checked yet: a live test against real Radmin peers, the Android app, the
Minecraft plugin and mod, and the Go server.

## Rebuilding OpenRad from the copy

```bash
git clone https://github.com/gabrielmf1998/openrad.git   # or the GitLab copy
cd openrad && git checkout v2.4.5
cargo fetch --locked
cargo build --release --locked --offline --workspace
# binaries in target/release: openrad, openrad-desktop
```

OpenRad and the Radmin-Linux VM both use the 26.0.0.0/8 range, so run only one
of them at a time.

## Updating the copies

The script used is
[`tools/mirror-releases.py`](../tools/mirror-releases.py). For each project,
fetch the upstream branches and tags into a `git clone --mirror`, push
`refs/heads/*` and `refs/tags/*` to both copies, then run
`tools/mirror-releases.py <upstream owner/repo> <name>`. It skips releases that
were already copied.
