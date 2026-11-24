# Changelog

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
