# Changelog

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
