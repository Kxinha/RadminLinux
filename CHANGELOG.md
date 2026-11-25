# Changelog

## 0.9.0 (2026-11-25)

### Verified with official Radmin VPN
* Tested the native engine against the official Radmin VPN app on Windows (new
  identities on both sides): create and join in both directions, ping, TCP and
  UDP both ways, grant admin, remove members, delete network. Results in the
  README.

### Firewall Exceptions (System menu, like Radmin)
* The test found the one thing that did not work: a Windows peer could ping a
  Linux PC but could not connect to a program on it, because firewalld (and
  ufw) refuse connections on the VPN interface.
* **Allow All Apps**: VPN peers can reach any program (firewalld zone
  "trusted", or `ufw allow in on radminvpn0`).
* **Add Application**: lists your programs waiting for connections and opens
  only their ports, only on the VPN interface (own firewalld zone
  "radmin-vpn", ping still answered). Checked items are open; uncheck to close.
* Asks for the admin password, survives reboot, the uninstaller removes it.
  Without firewalld or ufw the menu says "Linux Firewall is Off".

### Window
* No network yet: Create Network and Join Network buttons in the middle of the
  window, as in Radmin.

## 0.8.0 (2026-11-25)

The app now looks and works like Radmin VPN, with OpenRad running natively underneath.

### Main window
* Radmin's dark blue palette, its title bar, the round power button (ON/OFF),
  your name, IP and an Online badge on top.
* Networks are blue pills, members are rows with green signal bars (grey with an
  x when offline). Hover a member for its IP, status, channel and ping.
* Menus like Radmin: System (Change Name, Settings, Exit), Network (Create
  Network Ins, Join Network +), Help (Help F1, View Log, Report a Bug, Request a
  Feature, Check for Updates, About). F5 refreshes.
* Change Name edits the name in place, as in Radmin (Enter saves, Esc cancels).

### Dialogs
* Join Network with the Private Network and Gaming Network tabs. Gaming searches
  the public networks and shows how many members each has.
* Create Network with name, password and confirmation; Create stays disabled
  until it is valid.
* Network Properties and member Properties (role, connection status, channel).
  Admins change a member's role there; "Block traffic (Linux)" blocks an IP.
* Settings with General, Advanced and Notifications pages and On/Off switches.
  The Linux extras (relays only, retry failed peers, blocked addresses,
  diagnostics, reset identity) live in Advanced.

### Right click menus
* Network: Leave Network, Delete Network (admins), Copy Network Name, Properties.
* Member: Copy IP Address, Ping (opens a terminal), Remove, Grant or Revoke
  Admin Rights (admins), Properties. Double click opens Properties.
* Empty area: Create Network, Join Network.
* Tray: Open, Go Online or Go Offline, Exit.

### Not there, because OpenRad does not have it
* Chat, Disable Network, Radmin Server items, changing network parameters or
  password, firewall exceptions, IPv6.

## 0.7.0 (2026-11-25)

Native engine (OpenRad): what the official Radmin window did, now in the app.

### Network admin
* Your networks show a ★ when you are an admin, and members that are admins
  have a ★ too. A network you asked to join shows "(waiting for approval)".
* Right click a network you administer > Delete network for everyone. You type
  the network name to confirm.
* Right click a member > Manage in <network> > Make admin, Remove admin or
  Remove from the network (only in networks where you are an admin).

### Peers
* Right click a member > Ping now, and Block this IP: both ways, only what it
  sends or only what I send. Blocking is silent and local, the peer stays
  connected and is not told. Blocked rows say "(blocked)".
* Network > Blocked addresses… lists them and unblocks one.
* Network > Retry failed peers and Network > Use relays only (no direct
  UDP/TCP, for networks where direct connections fail; saved in the profile).

### Fixes
* Join and Create check that the password has 6 to 256 characters (Radmin's
  rule) before sending it.

## 0.6.0 (2026-11-25)

### Native engine: no VM
* New default engine: [OpenRad](https://github.com/gabrielmf1998/openrad)
  (copy of gringoestrangeiro/openrad, MIT), a native Radmin VPN client. The
  same window and tray drive it through `app/orctl.py`, which runs
  `openrad <command> --json` against the per user OpenRad service.
* Power button and tray: turn on (registers a new identity the first time,
  named after the PC; `RADMIN_OPENRAD_NODE_NAME` picks another name) and off
  (removes `radminvpn0`). Quit disconnects, then exits.
* Network > Join a network and Create network ask for the name and password in
  the app (no Radmin window needed). The password goes to OpenRad through a
  private file in `$XDG_RUNTIME_DIR` that is deleted right after.
  Leave this network from the network header or the menu.
* Rename node, Reset Radmin identity (the old profile is kept aside as
  `openrad.old-<date>`), System > Diagnostics (service, Radmin server, interface,
  polkit rule, peers connected, server ping) and System > Retry the network
  interface.
* Peers show UDP / TCP / TcpRelay and the ping time, as with the VM.
* OpenRad errors (wrong password, unknown network) are shown in a dialog.
  Actions sent while OpenRad reconnects (right after start or a rename) wait
  for it instead of failing, and join/leave wait until the network list
  really changed.

### Install
* `install.sh` picks the engine: `RADMIN_ENGINE`, else the one this install
  used before, else the VM when a VM image is installed, else native. Existing
  VM installs stay on the VM when they update.
* Native install: downloads the OpenRad release from our GitHub copy (GitLab
  copy as fallback), checks the sha256 pinned in `OPENRAD_VERSION`, installs it
  root owned in `/usr/local/lib/radmin-linux/` and adds a per user polkit rule
  (`49-radmin-linux-openrad-<user>.rules`) that allows only
  `openrad tap-helper` without a password. All root steps share one pkexec
  prompt; reinstalling the same version asks nothing. No KVM, qemu or VM image.
* Switching to native turns the VM link `radmin-bridge` off;
  `RADMIN_ENGINE=vm ./install.sh` turns it back on. The VM image is kept.
* `bootstrap.sh` skips the VM image download for the native engine.
* `uninstall.sh` stops OpenRad and removes its binary and polkit rule (one
  pkexec). The OpenRad identity in `~/.local/state/openrad` is kept.
* Packages: qemu, zstd, socat, NetworkManager and tigervnc are now optional
  (VM engine only); curl, iproute2 and polkit are required.
* The GitHub repository is now `gabrielmf1998/Radmin-Linux` (the old
  `Radmin-Linux-KDE` URLs redirect).

## 0.5.3 (2026-11-24)

* Quit (tray menu or File menu) leaves nothing behind. It turns the VM off
  (Windows gets up to 2 minutes, then the VM is stopped), closes the VNC
  windows showing the VM (also ones opened outside the app), cancels a VM
  start that is still running, and only then exits. Before, Quit closed only
  the app and the VM kept running in the background.
* Meanwhile the tray says "turning the VM off, then quitting", the menu entry
  reads "Quitting… (turning the VM off)" and only Show stays enabled. With the
  VM already off the app exits at once.
* Quit with the window open now really quits (Qt 6 cancelled the quit because
  the window refused to close and went to the tray instead).
* Logging out with the app open asks Windows to shut down; a small detached
  watcher stops the VM if it is still on after 2 minutes.
* Restarting after an app update still keeps the VM on.
* A VM process that has exited but was not reaped yet no longer counts as
  running, so a shutdown is never waited on for nothing.

## 0.5.2 (2026-11-24)

* While the VM shuts down the app says "Shutting down… / VM is turning off,
  please wait" (badge "Stopping", tray "shutting down") instead of
  "Starting… / VM is booting". Before, a VM that was going away looked the
  same as one coming up: the process was still there and the agent had stopped
  answering.
* Works for the power button, the tray menu and `bench-stop.sh`: the shutdown
  leaves a `stopping` file with the qemu pid in the VM folder while it runs.
* During the shutdown the power button only says it is shutting down,
  Connect and the tray power entry are disabled, the watchdog never
  "recovers" a VM that is shutting down, and there is no
  "Radmin VPN stopped" alert when it was turned off on purpose.
* Turning off from the app waits up to 2 minutes for Windows and then stops
  the VM, like `bench-stop.sh` (before, a stuck shutdown left it half off).

## 0.5.1 (2026-11-24)

### The list only shows what Radmin shows
* The app keeps the list empty ("reading the Radmin window…") until the VM
  agent has a complete reading, instead of filling it with old peers, guessed
  groups and networks you already left while Windows was still starting.
* The reader publishes only after the Radmin window has settled (two equal
  readings in a row), so a half loaded list never reaches the app.
* The old list (ARP, ping, member sync, groups) is used only when the agent in
  the VM is old and could not be updated; "Sync members" is hidden otherwise.
* "Leave network" opens the Radmin window instead of guessing the network
  from the registry, which still lists networks you left long ago.
* Manual mode (auto refresh off) no longer shows the old peer list.

### The VM gets to Radmin faster
* At every logon Windows showed "Activate Windows now / The activation period
  has expired" and held the desktop for about 2 minutes, so Radmin and the
  reader started late, and the dialog stayed on screen. The agent (version 6,
  new `logon-guard.ps1`) closes it after 3 s, the same as "Ask me later": the
  taskbar is up 8 s after logon instead of 118 s. Registry switches do not
  stop this dialog; an activation window opened by hand is left alone.
* The reader wakes the Radmin window every 10 s while it is missing (was 60 s)
  and minimizes it again even when it showed up late.
* A network is expanded only when Radmin shows it collapsed and empty, once.

## 0.5.0 (2026-11-24)

### The list now matches the Radmin window
* The VM agent (version 5) reads the real Radmin window through Windows UI
  Automation: network names, member names and member IPs, exactly what Radmin
  shows, in about 30 ms. Before, the app guessed from ARP (only peers that had
  already sent traffic), NetBIOS lookups (up to 3.6 s per refresh) and a
  ~200 MB memory dump of the Radmin window, so it showed stale peers from old
  networks and missed real members.
* Online comes from Radmin itself: the agent follows Radmin's own
  `service.log` (connected, disconnected, direct UDP/TCP or relay). Peers that
  block ping (most Windows PCs) no longer show as offline. Ping still adds
  the response time, and the row says "relay" when Radmin goes through one.
* One section per Radmin network with "online/total"; old peers that are not
  in any of your networks no longer show up.
* The list refreshes every 5 s while the window is open (`/state` is a file
  read in the VM now, ~15 ms). In the tray it stays at 60 s.
* "＋ Network" offers Join or Create; right click on a network copies its name
  or opens the Radmin window.
* Older agents keep working with the previous list (ARP, ping, member sync).

### How
* New `agent/gui-reader.ps1`, started at logon in the VM desktop session (task
  RadminAgent-GuiReader). It keeps the Radmin window running minimized (Qt
  hides the list from UI Automation while the window sits in the tray) and
  leaves a window you opened through VNC alone. About 50 MB of RAM and under
  1% of the VM's CPU; the VM stays at about 3% of one host core.
* The API restarts the reader when its output goes stale.

## 0.4.0 (2026-11-24)

### Updates
* **In-app updates**: Help > Check for app updates, plus a quiet daily check
  (one request to the GitHub releases API, GitLab as fallback; can be turned
  off in System > Settings). "Update now" runs the installer of that release
  in the background, keeps your VM and identity and offers to restart the app.
* App releases no longer re-upload the 2.4 GB VM image: `IMAGE_VERSION` names
  the release that carries it and `bootstrap.sh` downloads it from there.
* The VM agent is only ever upgraded, never downgraded by an older app.

### Window and tray
* Ping time next to every online peer; right click > Copy IP.
* Tray icon shows the state (green dot online, amber starting/disconnected,
  grey off) and the tooltip shows how many peers are online.
* Optional notification when Radmin or the VM stops on its own.
* System > Settings: start with the session (hidden in the tray), turn the
  VM on when the app opens, notifications, automatic update checks.
* Only one instance: opening the app again shows the running window.
* Status messages (action results, errors) stay readable for a few seconds
  instead of being overwritten by the next refresh.
* Leave network shows the network label, not only the GUID.
* Help > Open the install log, Report a problem.

### Lighter
* Peer rows are reused and only reordered when the order changes (before,
  every refresh and every ping destroyed and rebuilt the whole list).
* Hidden in the tray the app polls every 60 s instead of 15 s and draws nothing.
* The peer list file is written when something changes, not every 15 s.
* Inside the VM the agent asks WMI for the node address once a minute instead
  of on every refresh (`/state` 100 ms to 13 ms), and the automatic self repair
  runs every 10 min instead of every 2 min (the watchdog still checks every 2 min).

### Fixes
* Peer names with accents came out as garbage (NetBIOS names were decoded in
  the wrong codepage); old mangled names are cleaned.
* Closing the window on a desktop without a tray left an invisible process.
* An upgrade while the VM was running could re-create the tap link under it;
  the installer now applies link changes in place.
* The peer list file is written atomically.

## 0.3.0 (2026-11-24)

### Identity and privacy
* Every install gets its **own Radmin identity**. The 0.1.0 image carried the
  builder's node id, 26.x address and network list, so every download showed
  up as the same node with the same networks. The new release image has no
  identity; the agent gives each install a new one on first boot and resets
  images copied from someone else or from 0.1.0 (the app tells you once).
* New **System > Reset Radmin identity**.
* The release image no longer contains a browser, browsing data, logs, chat
  logs, crash dumps or the builder's computer name; free space is zeroed.
* `build-vm.sh --clean` now works on a throwaway offline copy and never
  touches the source VM.

### Speed ("Radmin is laggy")
* The app talks to a small API inside the VM: a refresh takes ~0.1 s instead
  of a 10 to 20 s WMI login that also spiked the CPU. Auto refresh is on again
  by default (every 15 s); WMI is only a fallback.
* VM tuned for Windows 7: Hyper-V enlightenments, HPET off, tick and RTC drift
  fixes, SSD + TRIM disk. 36 useless Windows services (IIS, FTP, SNMP, search,
  spooler…) and maintenance tasks are disabled. The idle VM uses about 2% of
  one core.
* No more CPU deprioritising of the VM (it starved the VPN while you played);
  only its disk I/O stays low priority.
* The watchdog probe runs off the UI thread, so the window never freezes.

### Screen ("the Radmin screen is broken and laggy")
* **Open the Radmin window** brings Radmin to the front inside the VM before
  the viewer opens, and the viewer uses lossless raw encoding with no remote
  resize (Windows 7 cannot follow it, the picture used to jump).

### Install ("the curl is bad")
* `bootstrap.sh` downloads the **latest release** (code and image from the same
  tag), shows a progress bar, **resumes** interrupted downloads, verifies every
  part against `SHA256SUMS`, checks free space and never writes the image to
  `/tmp` (RAM on most distros). An installed image is kept on upgrade.
* `RADMIN_MIRROR=gitlab` downloads from the GitLab mirror.
* `install.sh` asks for the password once, installs only what is missing
  (Arch: `qemu-base` instead of the full `qemu-desktop`), uses the smaller
  `PySide6-Essentials`, keeps your settings and install id on upgrade and logs
  to `install.log`. New `uninstall.sh`.

### Security
* Windows firewall on inside the VM (it was off while the VM had a public
  password): only the Linux link and pings get in.
* The Linux side of the link moved from firewalld zone `trusted` to `block`,
  so the VM cannot reach services on your PC.
* Radmin updates are only installed when the installer is signed by Famatech.

### Fixes
* The update check always failed in the app (a Qt signal turned its result
  into `True`).
* Radmin updates: correct silent switches, retries, and a clear message when
  Radmin 2.1 needs the Windows 7 SHA-2 update.
* The app now finishes booting the VM in manual mode too (it stopped polling).
* A stale pidfile whose number belonged to another program counted as a
  running VM.
* `env.sh` let `config.env` override environment variables.
* The agent upgrades itself in existing VMs when the app is newer.

## 0.2.0
* ICS/NAT link with the 26.0.0.0/8 route, password never expires, ghost ICS
  NIC cleanup.

## 0.1.0
* First release with the ready made VM image.
