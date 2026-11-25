# Native Linux ports of Radmin VPN

Radmin-Linux started by running the real Radmin VPN inside a small Windows 7 VM
(still available as the VM engine). Other people are writing **native** Linux
clients that speak the Radmin protocol directly,
with no VM. This page lists those projects and what was checked in them.

None of these projects are ours and none are affiliated with Famatech.

Note that the [Radmin VPN EULA](https://www.radmin-vpn.com/eula/) forbids
reverse engineering the official client. These projects were written and
published by their own authors; read their licenses and notes before using them.

## The projects

| Project | What it is | License | Upstream |
|---|---|---|---|
| **OpenRad** | Full Rust reimplementation: Linux TAP interface, desktop app, CLI, TCP, UDP and relay peers, Android app, self hosted server | MIT | [gringoestrangeiro/openrad](https://github.com/gringoestrangeiro/openrad) |
| radmin-linux-rs | Early Rust client, protocol research notes | MIT | [Pedrodroks34/radmin-linux-rs](https://github.com/Pedrodroks34/radmin-linux-rs) |
| radmin-vpn-linux | The official client under Wine with a custom driver and TAP bridge. The branch `marcelo1565/main` holds the Marcelo1565 fork | GPL-3.0 | [baptisterajaut/radmin-vpn-linux](https://github.com/baptisterajaut/radmin-vpn-linux) |
| radmin-on-linux | Radmin LAN/VPN on new and old Linux versions | GPL-3.0 | [Lackind/radmin-on-linux](https://github.com/Lackind/radmin-on-linux) |
| linmin-vpn | Tray front end (Rust + Slint) for the Radmin control service | GPL-3.0 | [LinCastr/linmin-vpn](https://github.com/LinCastr/linmin-vpn) |

The GitHub and GitLab copies made on 2026-11-24 were removed on 2026-11-25 to
keep the profile clean. Use the upstream links above. The OpenRad build that
Radmin-Linux installs lives in the Radmin-Linux releases, see
[`packaging/openrad`](../packaging/openrad/README.md).

`RhuanXDK/radmin-vpn-linux-rs`, an older fork of radmin-linux-rs, was already
deleted upstream.

## OpenRad review (v2.4.5, 2026-11-24)

What was checked before using it, without running anything on the real network:

* **Network.** The client only talks to the Radmin bootstrap server
  `148.113.190.78:17301` (in `src/config.rs`, overridable with `--host`), to the
  servers and peers that server hands out, and to
  `api.github.com/repos/gringoestrangeiro/openrad/releases/latest` for the
  update notice (through the system `curl`, HTTPS only). No other hosts or
  telemetry were found in `src/` and `desktop/src/`.
* **Root.** Only the short `openrad tap-helper` runs as root, through `sudo -n`
  or `pkexec`. It refuses addresses outside 26.0.0.0/8, opens `/dev/net/tun`,
  creates the `radminvpn0` TAP interface (not persistent), sets the 26.x address and
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

Live test (2026-11-24 and 25, new test identity, a public network):

* The service registered a new node and joined the network. 54 of 97 members
  connected within about 20 s: 36 direct UDP, 8 direct TCP, 10 through the
  Radmin relay.
* `radminvpn0` came up with the 26.x address. ICMP ping to peers: 23 ms over
  UDP, 28 ms over TCP, 291 ms through the relay, 0% loss.
* rename, search, leave, join, a wrong password and an unknown network (clear
  errors), ping and stop all worked from the CLI and from the Radmin-Linux app.
  Leave and join are applied a moment after their reply, and actions sent while
  the service reconnects answer "retry shortly"; the app waits for both.

Not checked yet: the Android app, the Minecraft plugin and mod, and the Go
server.

## OpenRad as the Radmin-Linux native engine (0.6.0)

Since 0.6.0 Radmin-Linux uses OpenRad as its default engine (see the README).
The installer takes the `openrad` binary from a Radmin-Linux release (GitHub,
GitLab as fallback). Since 0.9.2 that is OpenRad 2.4.5 plus the rename fix in
[`packaging/openrad`](../packaging/openrad/README.md). Version, sha256 and the
release that holds it are pinned in [`OPENRAD_VERSION`](../OPENRAD_VERSION), and installs it root owned in
`/usr/local/lib/radmin-linux/openrad`. Root ownership matters: the polkit rule
(`packaging/polkit/`) lets the user run only that path with `tap-helper` as the
first argument, so the user cannot swap the binary that runs as root.

Since 0.7.0 the app also drives the OpenRad admin and peer commands: `delete`,
`kick`, `grant-admin`, `revoke-admin` (roles: 0 pending, 1 member, 2 admin; only
admins may use them), `block`/`unblock` (stored in the profile preferences and
read back from `status`), `force-relay` and `retry-peers`. Not exposed yet:
`broadcast-peer`, `server`, `interface-name`.

To move to a newer OpenRad: check whether upstream already has the rename fix,
check what changed in `tap-helper`, build the tarball as in `packaging/openrad`,
attach it to a Radmin-Linux release, put tag, sha256 and release in
`OPENRAD_VERSION`, test, release.

## Rebuilding OpenRad

```bash
git clone https://github.com/gringoestrangeiro/openrad.git
cd openrad && git checkout v2.4.5
cargo fetch --locked
cargo build --release --locked --offline --workspace
# binaries in target/release: openrad, openrad-desktop
```

OpenRad and the Radmin-Linux VM both use the 26.0.0.0/8 range, so run only one
of them at a time.
