#!/usr/bin/env python3
"""
Radmin VPN (Linux) - a native window for the real Radmin VPN that runs headless in a
small Windows VM. Talks to the agent API inside the VM (instant), WMI only as fallback.
Shows the node, the service and the peers (online via ARP/ping, offline via roster);
peer nicknames and network groups are local.
"""
from __future__ import annotations
import sys, os, time
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QMenuBar, QMenu, QSystemTrayIcon, QInputDialog,
    QSizePolicy, QPushButton, QMessageBox,
)
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QGuiApplication
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtNetwork import QLocalServer, QLocalSocket

sys.path.insert(0, os.path.dirname(__file__))
import backend
import actions
import agent
import vmctl
import icons
import config
import updater
from roster import Roster

POLL_MS = 5000     # state refresh (agent API, ~50 ms: a file read in the VM)
PING_MS = 30000    # liveness (ping sweep) every 30s
# window hidden in the tray: nobody looks at the list, so poll 4x less
POLL_HIDDEN_MS = 60000
PING_HIDDEN_MS = 120000
UPDATE_CHECK_S = 24 * 3600   # automatic app update check, at most once a day
HEALTH_MS = 120000 # watchdog probe (one API ping) every 2 min
HEAL_EVERY_S = 600 # full diagnostics + auto-heal (starts PowerShell in the VM) every 10 min
BOOT_POLL_MS = 4000  # while the VM boots, check this often (cheap: API only)
MAX_ROWS = 300     # cap on peer rows (never create thousands of widgets)
# Watchdog (auto-restart a wedged VM) must NEVER fire during a normal boot:
#  - grace: no watchdog action for this long after a power-on (Windows boots ~1-2 min)
#  - strikes: only recover after this many CONSECUTIVE unresponsive health cycles,
#    i.e. a real hang, not a slow boot or a transient blip.
WATCHDOG_GRACE_S = 240
WATCHDOG_STRIKES = 3

QSS = """
* { color: #d9dde0; font-family: 'Segoe UI','Noto Sans',sans-serif; }
#root { background: #1d2023; }
#header { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
          stop:0 #163243, stop:1 #0d1c27); }
#logoText { color: #eaf6fa; font-size: 15px; font-weight: 700;
            letter-spacing: 2px; }
#menubar { background: #12262f; color: #c3ccd1; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #1f4a5f; }
QMenu { background: #22282c; border: 1px solid #333c42; }
QMenu::item:selected { background: #1f4a5f; }
#card { background: #232a2f; border-bottom: 1px solid #10151a; }
#nodeName { font-size: 14px; font-weight: 700; color: #f0f3f5; }
#nodeIp   { font-size: 13px; color: #8fa0aa; }
#badge { border-radius: 8px; padding: 1px 10px; font-size: 11px; font-weight: 600;
         background: #5a3030; color: #f2d6d6; }
#badge[kind="on"]   { background: #2f6b33; color: #d6f5d8; }
#badge[kind="warn"] { background: #6b5a30; color: #f5ecd6; }
QScrollArea { border: none; background: #1a1d20; }
#list { background: #1a1d20; }
#peerRow { background: #1a1d20; }
#peerRow:hover { background: #243139; }
.peerName { font-size: 13px; color: #e2e6e9; }
.peerName[off="true"] { color: #6b7378; }
.peerIp   { font-size: 12px; color: #8a99a2; }
.peerRtt  { font-size: 11px; color: #6f9f73; }
#netbar { background: #182028; border-bottom: 1px solid #10151a; }
.netchip { background: #22323d; color: #cfe6ef; border-radius: 9px;
           padding: 2px 10px; font-size: 11px; }
.netchipSel { background: #2f6f8c; color: #ffffff; border-radius: 9px;
              padding: 2px 10px; font-size: 11px; font-weight: 600; }
#nettools { background: #182028; border-bottom: 1px solid #10151a; }
#netAddBtn { background: #22323d; color: #cfe6ef; border: none; border-radius: 4px;
             padding: 3px 10px; font-size: 12px; font-weight: 600; }
#netAddBtn:hover { background: #2f6f8c; color: #ffffff; }
.netHeader { background: #1f2a33; border-top: 1px solid #10151a;
             border-bottom: 1px solid #10151a; }
.netHeaderName { color: #cfe6ef; font-size: 12px; font-weight: 700; letter-spacing: 1px; }
.netHeaderCount { color: #7f8c93; font-size: 11px; }
#status { background: #12262f; color: #7f8c93; font-size: 11px; padding: 3px 8px; }
QScrollBar:vertical { background: #16191c; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #3a444b; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


class Fetcher(QThread):
    got = Signal(object)

    def __init__(self, allow_wmi=False):
        super().__init__()
        self.allow_wmi = allow_wmi

    def run(self):
        self.got.emit(backend.fetch_state(allow_wmi=self.allow_wmi))


class Pinger(QThread):
    """Liveness ativo: pinga a lista de IPs conhecidos direto do Linux."""
    done = Signal(object)

    def __init__(self, ips):
        super().__init__()
        self.ips = ips

    def run(self):
        self.done.emit(actions.ping_rtt(self.ips))


class ActionWorker(QThread):
    # object, not bool/str: some actions return a dict (update check) and a typed
    # Signal(bool, ...) silently turned it into True.
    done = Signal(object, object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            ok, log = self.fn()
        except Exception as e:  # noqa
            ok, log = False, str(e)
        self.done.emit(ok, log)


class HealthWorker(QThread):
    """Diagnostics + auto-heal in the background."""
    done = Signal(object)

    def __init__(self, heal):
        super().__init__()
        self.heal = heal

    def run(self):
        self.done.emit(agent.health(heal=self.heal))


class WatchWorker(QThread):
    """Watchdog probe off the UI thread: is the VM process alive, does it answer?"""
    done = Signal(bool, bool)

    def run(self):
        alive = vmctl.is_running()
        self.done.emit(alive, vmctl.is_responsive() if alive else False)


class EnsureAgentWorker(QThread):
    """Install/upgrade the in-VM agent when its version differs from this app's."""
    done = Signal(str)

    def __init__(self, force=False):
        super().__init__()
        self.force = force

    def run(self):
        self.done.emit(agent.ensure_agent(force=self.force, log=lambda m: None))


class DiscoverWorker(QThread):
    """Discovers the complete peer list (GUI dump) - heavy, occasional."""
    done = Signal(object)

    def run(self):
        import discover
        self.done.emit(discover.discover_peers())


class NetHeader(QFrame):
    """Collapsible network-section header: click toggles, right-click manages."""
    def __init__(self, gid, name, count, collapsed, on_toggle, on_menu):
        super().__init__()
        self.gid = gid
        self.setProperty("class", "netHeader")
        self.setObjectName("netHeader")
        self.setFixedHeight(28)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        lay = QHBoxLayout(self); lay.setContentsMargins(10, 2, 10, 2); lay.setSpacing(6)
        self.arrow = QLabel("▶" if collapsed else "▼")
        self.arrow.setStyleSheet("color:#7f8c93; font-size:10px;")
        lay.addWidget(self.arrow)
        self.nm = QLabel(name); self.nm.setProperty("class", "netHeaderName")
        lay.addWidget(self.nm)
        self.cnt = QLabel(f"· {count}"); self.cnt.setProperty("class", "netHeaderCount")
        lay.addWidget(self.cnt)
        lay.addStretch(1)
        self._on_toggle = on_toggle
        self._on_menu = on_menu

    def update_info(self, name, count, collapsed):
        self.nm.setText(name)
        self.cnt.setText(f"· {count}")
        self.arrow.setText("▶" if collapsed else "▼")

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton and self._on_menu:
            self._on_menu(self.gid, e.globalPosition().toPoint())
        else:
            self._on_toggle(self.gid)


def _repolish(w):
    st = w.style(); st.unpolish(w); st.polish(w)


class PeerRow(QFrame):
    """One peer. Rows are reused across refreshes: set_state() only touches what
    changed, so a refresh no longer rebuilds hundreds of widgets."""
    def __init__(self, ip, on_rename, on_menu=None, key=None):
        super().__init__()
        self.ip = ip
        self.key = key or ip      # the same peer can be in two networks
        self._on_menu = on_menu
        self._on_rename = on_rename
        self.setObjectName("peerRow")
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedHeight(34)
        self._online = None
        self._label = None
        self._rtt = None
        self._via = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(8)
        self.sig = QLabel(); self.sig.setFixedWidth(20)
        lay.addWidget(self.sig)
        self.nameLbl = QLabel(); self.nameLbl.setProperty("class", "peerName")
        lay.addWidget(self.nameLbl, 1)
        self.rttLbl = QLabel(); self.rttLbl.setProperty("class", "peerRtt")
        lay.addWidget(self.rttLbl)
        self.ipLbl = QLabel(ip); self.ipLbl.setProperty("class", "peerIp")
        self.ipLbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.ipLbl)

    def set_state(self, label, online, rtt=None, via=""):
        if online != self._online:
            self._online = online
            self.sig.setPixmap(icons.signal_pixmap(online, 18))
            self.nameLbl.setProperty("off", not online)
            _repolish(self.nameLbl)
        if label != self._label:
            self._label = label
            self.nameLbl.setText(label)
        rtt = rtt if online else None
        via = via if online else ""
        if rtt != self._rtt or via != self._via:
            self._rtt = rtt
            self._via = via
            relay = via.lower() == "tcprelay"
            txt = "" if rtt is None else (f"{rtt:.0f} ms" if rtt >= 1 else "<1 ms")
            if relay:
                txt = (txt + " · relay") if txt else "relay"
            self.rttLbl.setText(txt)
            how = {"udp": "direct (UDP)", "tcp": "direct (TCP)", "tcprelay": "through a Radmin relay"}.get(via.lower(), "")
            self.setToolTip(f"{self.ip}" + ("" if rtt is None else f" · ping {rtt:.1f} ms")
                            + (f" · {how}" if how else ""))

    def mouseDoubleClickEvent(self, e):
        self._on_rename(self.ip)

    def contextMenuEvent(self, e):
        if self._on_menu:
            self._on_menu(self.ip, e.globalPos())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.roster = Roster()
        self.setWindowTitle("Radmin VPN")
        self.setWindowIcon(icons.logo_icon(64))
        self.resize(300, 480)
        self._fetcher = None
        self._pinger = None
        self._action = None
        self._threads = set()   # all live QThreads, for shutdown to wait on
        self._busy = False
        self._booting = False   # VM on but WMI not answering yet (booting)
        self._closing = False
        self._last_service = "Unknown"
        self._vm_running = False
        self._vm_started_at = 0.0        # monotonic time of the last power-on (watchdog grace)
        self._unresponsive_strikes = 0   # consecutive unresponsive health cycles
        self._node_ip = ""
        self._networks = []       # GUIDs of the networks the node is in
        self._selected_net = None  # network selected in the filter (None = all)
        # Auto-refresh is ON by default now: a query is one tiny HTTP request to the
        # agent API (it used to be a whole wmiexec login, 10-20 s of CPU, which is
        # why 0.2 shipped a manual mode). Turn it off in System > Auto-refresh.
        self._auto = bool(self.roster.get_setting("auto_refresh_v3", True))
        self._ensure = None
        self._agent_checked = False   # agent version compared once per VM boot
        self._identity_seen = ""
        self._rows = {}            # ip -> PeerRow, reused across refreshes
        self._headers = {}         # group key -> NetHeader
        self._layout_sig = None
        self._list_dirty = False
        self._online = set()
        self._ping_online = set()   # answered the last ping sweep
        self._pinged = set()        # were in the last ping sweep (ping beats ARP)
        self._rtt = {}
        self._live = None          # networks as the Radmin window shows them (agent 5+)
        self._live_online = set()  # members Radmin is connected to (service.log)
        self._live_via = {}        # ip -> UDP / TCP / TcpRelay
        self._hidden_rows = 0
        self._n_online = 0
        self._summary = ""
        self._flash_until = 0.0
        self._user_stopped = False   # the user disconnected / turned off: no alert
        self._was_online = False
        self._upd = None
        self._update_offer = None
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.pingtimer = QTimer(self)
        self.pingtimer.timeout.connect(self.ping_now)
        self._health = None
        self._last_health = None
        self._last_heal = time.monotonic() - HEAL_EVERY_S + 180   # first one ~3 min after start
        self._discover = None
        self.healthtimer = QTimer(self)
        self.healthtimer.timeout.connect(self.health_now)
        # heavy discovery (~200MB dump) only on first run with an empty roster AND
        # only in auto mode; in manual mode nothing queries until the user asks.
        self._want_discover = self._auto and not self.roster.all_ips()
        self._watch = None
        if self._auto:
            self._start_auto_timers()
            self.refresh()
        else:
            self._show_manual_idle()
            if self._vm_running:
                self.refresh()     # one cheap API query; no WMI unless asked
        # optional: power the VM on when the app opens
        if self.roster.get_setting("vm_on_start", False) and not vmctl.is_running():
            QTimer.singleShot(500, self.do_vm_on)
        # daily app update check (a few seconds after start, never blocks)
        self.updtimer = QTimer(self)
        self.updtimer.timeout.connect(lambda: self.check_app_update(manual=False))
        self.updtimer.start(3600 * 1000)
        QTimer.singleShot(20000, lambda: self.check_app_update(manual=False))

    # ---------- auto vs manual querying ----------
    def _start_auto_timers(self):
        vis = self.isVisible()
        self.timer.start(POLL_MS if vis else POLL_HIDDEN_MS)
        self.pingtimer.start(PING_MS if vis else PING_HIDDEN_MS)
        self.healthtimer.start(HEALTH_MS)

    def showEvent(self, e):
        super().showEvent(e)
        if self._auto:
            self._start_auto_timers()
        if self._list_dirty:
            self._rebuild_list()
        if self._auto and self._vm_running:
            QTimer.singleShot(0, self.refresh)
            QTimer.singleShot(0, self.ping_now)

    def hideEvent(self, e):
        super().hideEvent(e)
        if self._auto:
            self._start_auto_timers()

    def _stop_auto_timers(self):
        self.timer.stop()
        self.pingtimer.stop()
        self.healthtimer.stop()

    def _show_manual_idle(self):
        """Manual mode on open: show the VM power state from the pidfile ONLY
        (vmctl.is_running reads a file — no WMI, no wmiexec, nothing heavy). The
        app stays idle until the user clicks Refresh or enables Auto-refresh."""
        try:
            running = vmctl.is_running()
        except Exception:  # noqa
            running = False
        self._vm_running = running
        self._booting = False
        self.nodeName.setText("Radmin VPN" if running else "VM is off")
        self.nodeIp.setText("Refresh to query the VM" if running else "click power to turn on")
        self._set_badge("Manual" if running else "Off", "warn" if running else "off")
        self.power.setPixmap(icons.power_pixmap(running, 48))
        self._sync_actions(False)
        self._update_tray()
        # show the known peers (as offline) so you can organize them into networks
        # without querying; a Refresh will light up who is actually online.
        self._render_peers(set())
        self._set_summary("● manual mode, Refresh to query" + ("" if running else " · VM off"))

    def _toggle_auto(self, on: bool):
        self._auto = bool(on)
        self.roster.set_setting("auto_refresh_v3", self._auto)
        if self._auto:
            self._flash("● auto-refresh ON")
            self._start_auto_timers()
            self.refresh()
        else:
            self._stop_auto_timers()
            self._show_manual_idle()
            self._flash("● auto-refresh OFF, Refresh to query")

    # ---------- layout ----------
    def _build(self):
        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        # menubar
        mb = QMenuBar(); mb.setObjectName("menubar")
        for name in ("System", "Network", "Help"):
            m = QMenu(name, mb)
            if name == "System":
                a = QAction("Refresh now", self); a.triggered.connect(self.refresh_full); m.addAction(a)
                self.actAuto = QAction("Auto-refresh", self)
                self.actAuto.setCheckable(True); self.actAuto.setChecked(self._auto)
                self.actAuto.setToolTip("Refresh every 15 s (one tiny request to the VM).")
                self.actAuto.toggled.connect(self._toggle_auto); m.addAction(self.actAuto)
                m.addSeparator()
                self.actVmOn = QAction("Turn VM on", self)
                self.actVmOn.triggered.connect(self.do_vm_on); m.addAction(self.actVmOn)
                self.actVmOff = QAction("Turn VM off", self)
                self.actVmOff.triggered.connect(self.do_vm_off); m.addAction(self.actVmOff)
                m.addSeparator()
                ah = QAction("Full diagnostics (auto-repair)", self)
                ah.triggered.connect(self.do_health); m.addAction(ah)
                au = QAction("Check for Radmin update…", self)
                au.triggered.connect(self.do_check_update); m.addAction(au)
                ao = QAction("Repair network (orchestrator)", self)
                ao.triggered.connect(self.do_orchestrate); m.addAction(ao)
                ai = QAction("Reinstall the VM agent", self)
                ai.triggered.connect(self.do_install_agent); m.addAction(ai)
                m.addSeparator()
                ar = QAction("Reset Radmin identity…", self)
                ar.setToolTip("New node id and 26.x address, no networks")
                ar.triggered.connect(self.do_reset_identity); m.addAction(ar)
                m.addSeparator()
                sm = m.addMenu("Settings")
                self.actAutostart = QAction("Start with my session (in the tray)", self)
                self.actAutostart.setCheckable(True); self.actAutostart.setChecked(autostart_enabled())
                self.actAutostart.toggled.connect(self._toggle_autostart); sm.addAction(self.actAutostart)
                self.actVmOnStart = QAction("Turn the VM on when the app opens", self)
                self.actVmOnStart.setCheckable(True)
                self.actVmOnStart.setChecked(bool(self.roster.get_setting("vm_on_start", False)))
                self.actVmOnStart.toggled.connect(lambda on: self.roster.set_setting("vm_on_start", bool(on)))
                sm.addAction(self.actVmOnStart)
                self.actNotify = QAction("Notify when the connection drops", self)
                self.actNotify.setCheckable(True)
                self.actNotify.setChecked(bool(self.roster.get_setting("notify", True)))
                self.actNotify.toggled.connect(lambda on: self.roster.set_setting("notify", bool(on)))
                sm.addAction(self.actNotify)
                self.actAutoUpd = QAction("Check for app updates automatically", self)
                self.actAutoUpd.setCheckable(True)
                self.actAutoUpd.setChecked(bool(self.roster.get_setting("auto_update_check", True)))
                self.actAutoUpd.setToolTip("Once a day, one request to the GitHub releases API")
                self.actAutoUpd.toggled.connect(lambda on: self.roster.set_setting("auto_update_check", bool(on)))
                sm.addAction(self.actAutoUpd)
                m.addSeparator()
                q = QAction("Quit", self); q.triggered.connect(self._quit); m.addAction(q)
            elif name == "Network":
                self.actOnlineOnly = QAction("Show online only", self)
                self.actOnlineOnly.setCheckable(True)
                self.actOnlineOnly.setChecked(bool(self.roster.get_setting("online_only", False)))
                self.actOnlineOnly.setToolTip("Hide offline peers from the list")
                self.actOnlineOnly.toggled.connect(self._toggle_online_only)
                m.addAction(self.actOnlineOnly)
                m.addSeparator()
                self.actConnect = QAction("Connect", self)
                self.actConnect.triggered.connect(self.do_connect); m.addAction(self.actConnect)
                self.actDisconnect = QAction("Disconnect", self)
                self.actDisconnect.triggered.connect(self.do_disconnect); m.addAction(self.actDisconnect)
                m.addSeparator()
                self.actRename = QAction("Rename this node…", self)
                self.actRename.triggered.connect(self.do_rename_node); m.addAction(self.actRename)
                self.actSync = QAction("Sync network members", self)
                self.actSync.triggered.connect(self.discover_now); m.addAction(self.actSync)
                m.addSeparator()
                self.actLeave = QAction("Leave network…", self)
                self.actLeave.triggered.connect(self.do_leave); m.addAction(self.actLeave)
                self.actJoin = QAction("Join a network…", self)
                self.actJoin.triggered.connect(self.do_join); m.addAction(self.actJoin)
                self.actCreate = QAction("Create network…", self)
                self.actCreate.triggered.connect(self.do_create); m.addAction(self.actCreate)
                m.addSeparator()
                self.actGui = QAction("Open the Radmin window", self)
                self.actGui.triggered.connect(self._open_vnc); m.addAction(self.actGui)
            elif name == "Help":
                a = QAction("Check for app updates…", self)
                a.triggered.connect(lambda: self.check_app_update(manual=True)); m.addAction(a)
                a = QAction("Open the install log", self)
                a.triggered.connect(lambda: self._open_path(os.path.join(config.HOME, "install.log"))); m.addAction(a)
                a = QAction("Report a problem…", self)
                a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(
                    f"https://github.com/{updater.GITHUB}/issues"))); m.addAction(a)
                m.addSeparator()
                a = QAction("About", self); a.triggered.connect(self._about); m.addAction(a)
            mb.addMenu(m)
        v.addWidget(mb)

        # header com logo
        header = QWidget(); header.setObjectName("header"); header.setFixedHeight(46)
        hl = QHBoxLayout(header); hl.setContentsMargins(12, 0, 12, 0); hl.setSpacing(8)
        logo = QLabel(); logo.setPixmap(icons.logo_pixmap(26)); hl.addWidget(logo)
        t = QLabel("RADMIN VPN"); t.setObjectName("logoText"); hl.addWidget(t)
        hl.addStretch(1)
        # declared footprint: the VM is intentionally small so it never hogs the host
        foot = QLabel(config.vm_footprint())
        foot.setStyleSheet("color:#6f8290; font-size:10px; font-weight:600;")
        foot.setToolTip("The Windows VM uses only this much RAM/CPU — it won't hog your machine")
        hl.addWidget(foot)
        v.addWidget(header)

        # card do no
        card = QWidget(); card.setObjectName("card"); card.setFixedHeight(72)
        cl = QHBoxLayout(card); cl.setContentsMargins(14, 8, 14, 8); cl.setSpacing(12)
        self.power = QLabel(); self.power.setPixmap(icons.power_pixmap(False, 48))
        self.power.setCursor(QCursor(Qt.PointingHandCursor))
        self.power.setToolTip("Click to turn the VM on / off")
        self.power.mousePressEvent = lambda e: self.toggle_power()
        cl.addWidget(self.power)
        box = QVBoxLayout(); box.setSpacing(2)
        self.nodeName = QLabel("—"); self.nodeName.setObjectName("nodeName")
        self.nodeName.setCursor(QCursor(Qt.PointingHandCursor))
        self.nodeName.setToolTip("Double-click to rename this node")
        self.nodeName.mouseDoubleClickEvent = lambda e: self.do_rename_node()
        self.nodeIp = QLabel("—"); self.nodeIp.setObjectName("nodeIp")
        rowb = QHBoxLayout(); rowb.setSpacing(6)
        self.badge = QLabel("Offline"); self.badge.setObjectName("badge")
        self.badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        rowb.addWidget(self.badge); rowb.addStretch(1)
        box.addWidget(self.nodeName); box.addWidget(self.nodeIp); box.addLayout(rowb)
        cl.addLayout(box, 1)
        v.addWidget(card)

        # networks toolbar (dedicated control up top): create/manage network groups.
        # Peers are shown in collapsible per-network sections; assignment is MANUAL
        # (right-click a peer -> Move to). Grouping is local, stored in the roster.
        self.nettools = QWidget(); self.nettools.setObjectName("nettools")
        ntl = QHBoxLayout(self.nettools); ntl.setContentsMargins(8, 4, 8, 4); ntl.setSpacing(6)
        lbl = QLabel("Networks"); lbl.setStyleSheet("color:#8fa0aa; font-size:11px; font-weight:600;")
        ntl.addWidget(lbl); ntl.addStretch(1)
        self.addNetBtn = QPushButton("＋ Network"); self.addNetBtn.setObjectName("netAddBtn")
        self.addNetBtn.setCursor(QCursor(Qt.PointingHandCursor))
        self.addNetBtn.setToolTip("Create a network group, then right-click a peer to move it in")
        self.addNetBtn.clicked.connect(self._add_group)
        ntl.addWidget(self.addNetBtn)
        v.addWidget(self.nettools)

        # peer list
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.listw = QWidget(); self.listw.setObjectName("list")
        self.listv = QVBoxLayout(self.listw)
        self.listv.setContentsMargins(0, 4, 0, 4); self.listv.setSpacing(0)
        self.listv.addStretch(1)
        self.scroll.setWidget(self.listw)
        v.addWidget(self.scroll, 1)

        # status bar
        self.status = QLabel("starting…"); self.status.setObjectName("status")
        v.addWidget(self.status)

        self.setStyleSheet(QSS)
        self._build_tray()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(icons.logo_icon(64), self)
        self.tray.setToolTip("Radmin VPN (Linux)")
        menu = QMenu()
        show = QAction("Show", self); show.triggered.connect(self._show_raise); menu.addAction(show)
        menu.addSeparator()
        # full control from the tray — the user never touches the VM
        self.trayPower = QAction("Turn VM on", self)
        self.trayPower.triggered.connect(self.toggle_power); menu.addAction(self.trayPower)
        self.trayConn = QAction("Connect / Disconnect", self)
        self.trayConn.triggered.connect(self._tray_toggle_conn); menu.addAction(self.trayConn)
        menu.addSeparator()
        d = QAction("Full diagnostics (repair)", self); d.triggered.connect(self.do_health); menu.addAction(d)
        u = QAction("Check for update", self); u.triggered.connect(self.do_check_update); menu.addAction(u)
        s = QAction("Sync members", self); s.triggered.connect(self.discover_now); menu.addAction(s)
        r = QAction("Refresh", self); r.triggered.connect(self.refresh_full); menu.addAction(r)
        g = QAction("Open the Radmin window", self); g.triggered.connect(self._open_vnc); menu.addAction(g)
        menu.addSeparator()
        quit_ = QAction("Quit", self); quit_.triggered.connect(self._quit); menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_toggle_conn(self):
        if self._last_service == "Running":
            self.do_disconnect()
        else:
            self.do_connect()

    def _update_tray(self):
        """Reflect the state in the tray icon, tooltip and menu."""
        if not self._vm_running:
            tip, state = "Radmin VPN · VM off", "off"
        elif self._booting:
            tip, state = "Radmin VPN · starting", "warn"
        elif self._last_service == "Running":
            tip, state = f"Radmin VPN · online {self._node_ip} · {self._n_online} peers online", "on"
        else:
            tip, state = "Radmin VPN · disconnected", "warn"
        if tip != self.tray.toolTip():
            self.tray.setToolTip(tip)
        if state != getattr(self, "_tray_state", None):
            self._tray_state = state
            self.tray.setIcon(icons.tray_icon(state))
        if hasattr(self, "trayConn"):
            self.trayConn.setText("Disconnect" if self._last_service == "Running" else "Connect")
            self.trayConn.setEnabled(self._vm_running and not self._booting)
        if hasattr(self, "trayPower"):
            self.trayPower.setText("Turn VM off" if self._vm_running else "Turn VM on")

    def _notify(self, title, text):
        if self.roster.get_setting("notify", True) and self.tray.isVisible():
            self.tray.showMessage(title, text, icons.logo_icon(64), 8000)

    # ---------- worker threads ----------
    def _start(self, t):
        """Register the QThread and start it. Shutdown waits on all registered ones
        (the QThreads are not Qt children of the window, so findChildren missed them
        -> that is why the app aborted with a worker still running on close)."""
        self._threads.add(t)
        t.finished.connect(lambda: self._threads.discard(t))
        t.start()

    # ---------- data ----------
    def refresh(self, allow_wmi=False):
        if self._closing:
            return
        if self._fetcher and self._fetcher.isRunning():
            return
        if allow_wmi:
            self._flash("querying the VM…")
        self._fetcher = Fetcher(allow_wmi=allow_wmi)
        self._fetcher.got.connect(self._apply)
        self._start(self._fetcher)

    def refresh_full(self):
        """Manual refresh: may fall back to WMI (old agent) and re-checks the agent."""
        self._agent_checked = False
        self.refresh(allow_wmi=True)

    def _maybe_ensure_agent(self, st):
        """Once per boot: upgrade the in-VM agent if it is older than this app."""
        if self._agent_checked or self._closing:
            return
        if self._ensure and self._ensure.isRunning():
            return
        if st.ok and not agent._older(st.api, config.agent_version()):
            self._agent_checked = True
            return
        self._agent_checked = True
        self._flash("● updating the VM agent…")
        self._ensure = EnsureAgentWorker()
        self._ensure.done.connect(self._ensure_done)
        self._start(self._ensure)

    def _ensure_done(self, res):
        if self._closing:
            return
        self._flash({"ok": "● VM agent up to date", "upgraded": "● VM agent updated",
                             "failed": "● could not update the VM agent (System > Reinstall)"}.get(res, res))
        QTimer.singleShot(1500, self.refresh)

    def _check_identity_notice(self, st):
        note = st.identity_reset
        if not note or note == self._identity_seen or self._closing:
            return
        self._identity_seen = note
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "New Radmin identity",
            "This VM got its own Radmin VPN identity (new node and new 26.x address).\n\n"
            "Join your networks again with Network > Join a network.\n\n"
            f"Reason: {note}")
        actions.ack_identity()

    def _set_badge(self, text, kind):
        self.badge.setText(text)
        if self.badge.property("kind") != kind:
            self.badge.setProperty("kind", kind)
            _repolish(self.badge)

    def _apply(self, st: backend.State):
        if self._closing:
            return
        was_running = self._vm_running
        self._vm_running = st.vm_running
        if st.vm_running and not was_running:
            # VM just came up (or app opened while it was already booting): start the
            # watchdog grace so a slow boot is never mistaken for a hang.
            self._vm_started_at = time.monotonic()
        if not st.ok:
            self._last_service = "Stopped"
            # qemu alive but the agent not answering yet = VM BOOTING (not off)
            self._booting = bool(st.vm_running)
            if not st.vm_running:
                self._agent_checked = False
                self.nodeName.setText("VM is off")
                self.nodeIp.setText("click power to turn on")
                self._set_badge("Off", "off")
                self.power.setPixmap(icons.power_pixmap(False, 48))
                if was_running and not self._user_stopped and not self._busy:
                    self._notify("Radmin VPN stopped", "The Windows VM is no longer running.")
                self._was_online = False
            else:
                self.nodeName.setText("Starting…")
                self.nodeIp.setText("VM is booting, please wait")
                self._set_badge("Starting", "warn")
                self.power.setPixmap(icons.power_pixmap(True, 48))
            self._set_summary(f"● {st.error}")
            self._sync_actions(False)
            self._update_tray()
            self._clear_list()
            # while booting, re-check often so the switch to online feels live.
            # Cheap (one HTTP connect to the VM), so it runs in manual mode too.
            if self._booting and not self._closing:
                QTimer.singleShot(BOOT_POLL_MS, self.refresh)
                # an old agent never brings the API up: upgrade it over WMI
                if time.monotonic() - self._vm_started_at > 180:
                    self._maybe_ensure_agent(st)
            return

        first = self._booting or not was_running or self._last_service != "Running"
        self._booting = False
        self._maybe_ensure_agent(st)
        self._check_identity_notice(st)
        self._last_service = st.service
        # the name peers see is Radmin's Alias, not the Windows hostname
        self.nodeName.setText(st.alias or st.hostname or "—")
        self.nodeIp.setText(st.node_ip or "—")
        self._node_ip = st.node_ip
        on = (st.service == "Running")
        if self._was_online and not on and not self._user_stopped and not self._busy:
            self._notify("Radmin VPN disconnected", "The Radmin service stopped in the VM.")
        if on:
            self._user_stopped = False
        self._was_online = on
        self.power.setPixmap(icons.power_pixmap(on, 48))
        self._set_badge("Online" if on else "Offline", "on" if on else "off")
        self._sync_actions(on)

        # agent 5+: the real member list, read from the Radmin window. ARP and
        # the GUI memory dump are only the fallback for older agents.
        live = self._take_live(st.radmin)
        new_peer = False
        if live is not None:
            for net in live:
                for m in net["members"]:
                    new_peer |= self.roster.seen(m["ip"], "", m["name"])
        else:
            # discover new peers via ARP (the agent already filters 26.0.0.1)
            for p in st.peers:
                if p.ip == "26.0.0.1":
                    continue
                new_peer |= self.roster.seen(p.ip, p.mac, p.host)
        if new_peer:
            self.roster.save()
        else:
            self.roster.save_soft()

        self._networks = list(st.networks)   # GUIDs the node is in (info only)

        # liveness: ARP marks who is up; the ping sweep (own timer) refines it.
        # Ping right away only when something changed, not on every refresh.
        if live is not None:
            self._render_peers(self._live_online | self._ping_online)
        else:
            arp = {p.ip for p in st.peers if p.ip != "26.0.0.1"}
            self._render_peers(self._ping_online | {ip for ip in arp if ip not in self._pinged})
        if not on:
            self._set_summary("● Radmin is disconnected (Network > Connect)")
        if first or new_peer:
            self.ping_now()

        # first discovery only now, with the VM confirmed on and the service up
        if getattr(self, "_want_discover", False) and on and live is None:
            self._want_discover = False
            QTimer.singleShot(3000, self.discover_now)

    def _take_live(self, r):
        """Keep the agent's view of the Radmin window. Returns the networks or
        None (old agent / reader not running: the ARP + roster fallback)."""
        if not isinstance(r, dict) or not r.get("ok") or not isinstance(r.get("networks"), list):
            was = self._live is not None
            self._live, self._live_online, self._live_via = None, set(), {}
            self.addNetBtn.setToolTip("Create a network group, then right-click a peer to move it in")
            if was:
                self.actSync.setVisible(True)
                self._rows_reset()
            return None
        nets, online, via = [], set(), {}
        for n in r["networks"]:
            if not isinstance(n, dict):
                continue
            ms = []
            for m in n.get("members") or []:
                ip = str(m.get("ip") or "")
                if not ip.startswith("26.") or ip == self._node_ip:
                    continue
                ms.append({"ip": ip, "name": str(m.get("name") or "")})
                if m.get("online"):
                    online.add(ip)
                    via[ip] = str(m.get("via") or "")
            nets.append({"name": str(n.get("name") or "?"), "members": ms})
        if self._live is None:
            self._rows_reset()
            self.addNetBtn.setToolTip("Join or create a Radmin network")
            self.actSync.setVisible(False)     # the GUI dump is not needed any more
        self._live, self._live_online, self._live_via = nets, online, via
        return nets

    def _rows_reset(self):
        """Switching between the live and the fallback list: start from scratch."""
        self._place([])
        self._layout_sig = None

    def _peer_ips(self):
        """IPs the list shows: Radmin's members (live) or the local roster."""
        if self._live is not None:
            return sorted({m["ip"] for n in self._live for m in n["members"]})
        return [ip for ip in self.roster.all_ips() if ip != self._node_ip and ip != "26.0.0.1"]

    def _render_peers(self, online_set, rtt=None):
        self._online = set(online_set)
        if rtt is not None:
            self._rtt = dict(rtt)
        self._rebuild_list()

    def _clear_list(self):
        """Empty the list (VM off/booting). Widgets are kept for reuse."""
        self._place([])
        self._layout_sig = None

    def _place(self, widgets):
        """Put exactly these widgets, in this order, above the stretch. Widgets
        that are no longer wanted are deleted; the others are only moved."""
        keep = set(map(id, widgets))
        while self.listv.count() > 1:
            w = self.listv.takeAt(0).widget()
            if w is not None and id(w) not in keep:
                if isinstance(w, PeerRow):
                    self._rows.pop(w.key, None)
                elif isinstance(w, NetHeader):
                    self._headers.pop(w.gid or "__unassigned__", None)
                w.deleteLater()
        for n, w in enumerate(widgets):
            self.listv.insertWidget(n, w)
            w.show()

    def _row(self, ip, section=None):
        key = f"{section}|{ip}" if section else ip
        r = self._rows.get(key)
        if r is None:
            r = self._rows[key] = PeerRow(ip, self._rename, self._peer_menu, key)
        r.set_state(self.roster.label_of(ip), ip in self._online, self._rtt.get(ip),
                    self._live_via.get(ip, ""))
        return r

    def _header(self, gid, name, count, collapsed):
        key = gid or "__unassigned__"
        h = self._headers.get(key)
        if h is None:
            h = self._headers[key] = NetHeader(gid, name, count, collapsed,
                                               self._toggle_group, self._group_menu)
        else:
            h.update_info(name, count, collapsed)
        return h

    def _rebuild_list(self):
        """Render peers in collapsible per-network sections (manual grouping).
        No user groups yet -> a plain flat list. Otherwise: one section per group
        (in order) + an 'Unassigned' section, each collapsible. Honors the
        Network > 'Show online only' filter. Rows are reused; while the window is
        hidden nothing is drawn (it catches up when shown)."""
        if not self.isVisible():
            self._list_dirty = True
            self._update_summary()
            return
        self._list_dirty = False
        if self._live is not None:
            return self._rebuild_live()
        online = self._online
        online_only = bool(self.roster.get_setting("online_only", False))
        peers = [ip for ip in self.roster.all_ips()
                 if ip != self._node_ip and ip != "26.0.0.1"]
        buckets: dict = {}
        for ip in peers:
            buckets.setdefault(self.roster.group_of(ip), []).append(ip)

        def visible(ips):
            ips = sorted(ips, key=lambda ip: (ip not in online, self.roster.label_of(ip).lower()))
            if online_only:
                ips = [ip for ip in ips if ip in online]
            return ips

        groups = self.roster.groups()
        total = sum(len(visible(b)) for b in buckets.values())
        widgets = []
        shown = 0
        if not groups:
            # no groups: flat list (headers would be noise); create groups via ＋
            for ip in visible(buckets.get(None, []))[:MAX_ROWS]:
                widgets.append(self._row(ip)); shown += 1
        else:
            sections = [(g["id"], g["name"]) for g in groups]
            if buckets.get(None):
                sections.append((None, "Unassigned"))
            for gid, gname in sections:
                ips = visible(buckets.get(gid, []))
                if online_only and not ips:
                    continue   # when filtering, don't show empty network headers
                collapsed = self.roster.is_collapsed(gid or "__unassigned__")
                widgets.append(self._header(gid, gname, len(ips), collapsed))
                if collapsed:
                    continue
                for ip in ips:
                    if shown >= MAX_ROWS:
                        break
                    widgets.append(self._row(ip)); shown += 1
        sig = tuple(map(id, widgets))
        if sig != self._layout_sig:          # order or set changed: move widgets
            self._place(widgets)
            self._layout_sig = sig
        self._hidden_rows = max(0, total - shown)
        self._update_summary()

    def _rebuild_live(self):
        """One section per Radmin network, in the window's order, with exactly
        its members. Online = Radmin is connected to them (or they answer ping)."""
        online = self._online
        online_only = bool(self.roster.get_setting("online_only", False))
        widgets, shown, total = [], 0, 0
        for net in self._live:
            key = "net:" + net["name"]
            ms = sorted(net["members"], key=lambda m: (m["ip"] not in online,
                                                       self.roster.label_of(m["ip"]).lower()))
            n_on = sum(1 for m in ms if m["ip"] in online)
            if online_only:
                ms = [m for m in ms if m["ip"] in online]
                if not ms:
                    continue
            total += len(ms)
            collapsed = self.roster.is_collapsed(key)
            widgets.append(self._header(key, net["name"], f"{n_on}/{len(net['members'])}", collapsed))
            if collapsed:
                continue
            for m in ms:
                if shown >= MAX_ROWS:
                    break
                widgets.append(self._row(m["ip"], key)); shown += 1
        sig = tuple(map(id, widgets))
        if sig != self._layout_sig:
            self._place(widgets)
            self._layout_sig = sig
        self._hidden_rows = max(0, total - shown)
        self._update_summary()

    def _update_summary(self):
        peers = self._peer_ips()
        nact = sum(1 for ip in peers if ip in self._online)
        self._n_online = nact
        if not self._vm_running or self._booting or self._last_service != "Running":
            return
        flt = " · online only" if self.roster.get_setting("online_only", False) else ""
        hidden = f" (+{self._hidden_rows} hidden)" if self._hidden_rows else ""
        if self._live is not None:
            if not self._live:
                self._set_summary("● no network yet · Network > Join or Create")
                self._update_tray()
                return
            nn = len(self._live)
            known = f"{len(peers)} members · {nn} network" + ("s" if nn != 1 else "")
        else:
            known = f"{len(peers)} known"
        self._set_summary(f"● {nact} online · {known}{flt}{hidden} · node {self._node_ip or '—'}")
        self._update_tray()

    # ---------- status line: summary + short-lived messages ----------
    def _set_summary(self, text):
        self._summary = text
        if time.monotonic() >= self._flash_until:
            self.status.setText(text)

    def _flash(self, text, secs=6.0):
        """A message (action result, error) that stays readable for a few seconds
        instead of being overwritten by the next refresh."""
        self.status.setText(text)
        self._flash_until = time.monotonic() + secs
        QTimer.singleShot(int(secs * 1000) + 50, self._unflash)

    def _unflash(self):
        if time.monotonic() >= self._flash_until and self._summary:
            self.status.setText(self._summary)

    # ---------- network groups (manual assignment) ----------
    def _add_group(self):
        if self._live is not None:
            # live list: the sections are Radmin's own networks
            m = QMenu(self)
            m.addAction("Join a network…", self.do_join)
            m.addAction("Create network…", self.do_create)
            m.exec(self.addNetBtn.mapToGlobal(self.addNetBtn.rect().bottomLeft()))
            return
        text, ok = QInputDialog.getText(self, "New network", "Network name:")
        if ok and text.strip():
            self.roster.add_group(text.strip())
            self._rebuild_list()

    def _toggle_online_only(self, on: bool):
        self.roster.set_setting("online_only", bool(on))
        self._rebuild_list()

    def _toggle_group(self, gid):
        key = gid or "__unassigned__"
        self.roster.set_collapsed(key, not self.roster.is_collapsed(key))
        self._rebuild_list()

    def _group_menu(self, gid, pos):
        if gid is None:
            return   # the Unassigned section has nothing to rename/delete
        if gid.startswith("net:"):     # a real Radmin network (live list)
            name = gid[4:]
            m = QMenu(self)
            m.addAction("Copy network name", lambda: (QGuiApplication.clipboard().setText(name),
                                                     self._flash(f"● {name} copied")))
            m.addAction("Open the Radmin window", self._open_vnc)
            m.exec(pos)
            return
        m = QMenu(self)
        m.addAction("Rename…", lambda: self._rename_group(gid))
        m.addAction("Delete network", lambda: self._delete_group(gid))
        m.exec(pos)

    def _rename_group(self, gid):
        text, ok = QInputDialog.getText(self, "Rename network", "Network name:",
                                        text=self.roster.group_name(gid))
        if ok and text.strip():
            self.roster.rename_group(gid, text.strip())
            self._rebuild_list()

    def _delete_group(self, gid):
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(self, "Delete network",
            f"Delete '{self.roster.group_name(gid)}'?\n"
            "Its peers become Unassigned (they are not removed).")
        if r == QMessageBox.Yes:
            self.roster.remove_group(gid)
            self._rebuild_list()

    def _peer_menu(self, ip, pos):
        m = QMenu(self)
        m.addAction(f"Copy IP  {ip}", lambda: (QGuiApplication.clipboard().setText(ip),
                                              self._flash(f"● {ip} copied")))
        m.addAction("Rename…", lambda: self._rename(ip))
        if self._live is not None:
            m.exec(pos)
            return
        move = m.addMenu("Move to network")
        cur = self.roster.group_of(ip)
        for g in self.roster.groups():
            act = move.addAction(g["name"], lambda gid=g["id"]: self._move_peer(ip, gid))
            act.setCheckable(True); act.setChecked(cur == g["id"])
        move.addSeparator()
        move.addAction("＋ New network…", lambda: self._move_to_new(ip))
        if cur is not None:
            m.addAction("Unassign", lambda: self._move_peer(ip, None))
        m.exec(pos)

    def _move_peer(self, ip, gid):
        self.roster.assign(ip, gid)
        self._rebuild_list()

    def _move_to_new(self, ip):
        text, ok = QInputDialog.getText(self, "New network", "Network name:")
        if ok and text.strip():
            gid = self.roster.add_group(text.strip())
            self.roster.assign(ip, gid)
            self._rebuild_list()

    def _rename(self, ip):
        cur = self.roster.name_of(ip)
        text, ok = QInputDialog.getText(self, "Peer nickname",
                                        f"Name for {ip}:", text=cur)
        if ok:
            self.roster.set_name(ip, text.strip())
            self._rebuild_list()
            self.ping_now()

    # ---------- liveness (ping sweep from Linux) ----------
    def ping_now(self):
        # with no VM on there is no mesh to ping: avoids the pointless sweep (and the
        # background ping-process churn when the VM is off).
        if self._closing or not self._vm_running:
            return
        if self._pinger and self._pinger.isRunning():
            return
        ips = self._peer_ips()
        if not ips:
            return
        self._pinger = Pinger(ips)
        self._pinger.done.connect(self._apply_ping)
        self._start(self._pinger)

    def _apply_ping(self, result: dict):
        if self._closing or not self._vm_running:
            return
        online = {ip for ip, rtt in result.items() if rtt is not None}
        self._pinged = set(result)
        self._ping_online = online
        self._render_peers(online | self._live_online, {ip: rtt for ip, rtt in result.items() if rtt is not None})

    # ---------- full-list discovery (GUI dump) ----------
    def discover_now(self):
        if self._closing or not self._vm_running:
            return
        if self._discover and self._discover.isRunning():
            return
        self._flash("● discovering network members…")
        self._discover = DiscoverWorker()
        self._discover.done.connect(self._apply_discover)
        self._start(self._discover)

    def _apply_discover(self, peers: dict):
        if self._closing:
            return
        if not peers:
            self._flash("● member sync failed (VM/dump)")
            return
        novos = self.roster.ingest(peers)
        self._rebuild_list()
        self.ping_now()
        self._flash(f"● {len(peers)} members synced ({novos} new)")

    # ---------- health / auto-heal (silent, in the background) ----------
    def health_now(self):
        if self._closing or self._busy:
            return
        if self._watch and self._watch.isRunning():
            return
        # the probe (pidfile + API/ICMP) runs in a thread: never block the UI
        self._watch = WatchWorker()
        self._watch.done.connect(self._watch_result)
        self._start(self._watch)

    def _watch_result(self, alive, responsive):
        # --- watchdog: restart ONLY a genuinely wedged VM, never during boot ---
        # A slow Windows boot looks "unresponsive" too: skip while booting, skip
        # during the post-power-on grace, and require several consecutive
        # unresponsive cycles before touching the VM.
        if self._closing or self._busy:
            return
        if alive:
            in_grace = (time.monotonic() - self._vm_started_at) < WATCHDOG_GRACE_S
            if not self._booting and not in_grace and not responsive:
                self._unresponsive_strikes += 1
            else:
                self._unresponsive_strikes = 0
            if self._unresponsive_strikes >= WATCHDOG_STRIKES:
                self._unresponsive_strikes = 0
                self._flash("● VM unresponsive — recovering…")
                self._busy = True
                self._action = ActionWorker(lambda: (vmctl.recover() != "ok", "watchdog"))
                self._action.done.connect(lambda ok, log: self._vm_action_done("Recovering VM"))
                self._start(self._action)
                return
        else:
            self._unresponsive_strikes = 0
        # normal auto-heal: only when the VM is really up, not booting, and not
        # more often than HEAL_EVERY_S (each run starts a PowerShell in the VM)
        if not self._vm_running or self._booting:
            return
        if self._health and self._health.isRunning():
            return
        if time.monotonic() - self._last_heal < HEAL_EVERY_S:
            return
        self._last_heal = time.monotonic()
        self._health = HealthWorker(heal=True)   # always heal in the automatic cycle
        self._health.done.connect(self._apply_health)
        self._start(self._health)

    def _apply_health(self, res: dict):
        if self._closing:
            return
        self._last_health = res
        if res.get("error"):
            return
        # if something was healed, note it discreetly in the status bar
        if res.get("healed"):
            healed = [c["name"] for c in res.get("checks", []) if c.get("healed")]
            self._flash("● auto-repaired: " + ", ".join(healed))
            QTimer.singleShot(1000, self.refresh)

    def do_health(self):
        # manual diagnostics with a visible report
        if self._busy:
            return
        self._busy = True
        self._flash("● full diagnostics…")
        self._sync_actions(self._last_service == "Running")
        self._health = HealthWorker(heal=True)
        self._health.done.connect(self._health_report)
        self._start(self._health)

    def _health_report(self, res: dict):
        from PySide6.QtWidgets import QMessageBox
        self._busy = False
        self._sync_actions(self._last_service == "Running")
        if res.get("error"):
            QMessageBox.warning(self, "Diagnostics", f"Error: {res['error']}")
            return
        nomes = {
            "radmin_service": "Radmin service",
            "mesh_ip": "Mesh IP (26.x)",
            "ics": "Sharing (ICS)",
            "isolated_ip": "Bridge to Linux",
            "power_guard": "Power locked",
            "agent_tasks": "Boot agent",
            "api": "Agent API",
            "firewall": "VM firewall",
        }
        linhas = []
        for c in res.get("checks", []):
            mark = "✓" if c.get("ok") else "✗"
            extra = "  (repaired)" if c.get("healed") else ""
            linhas.append(f"{mark}  {nomes.get(c['name'], c['name'])}{extra}")
        titulo = "All systems OK" if res.get("all_ok") else "Problems found"
        m = QMessageBox(self)
        m.setWindowTitle("VM diagnostics")
        m.setIcon(QMessageBox.Information if res.get("all_ok") else QMessageBox.Warning)
        m.setText(titulo + (" — auto-repaired" if res.get("healed") else ""))
        m.setInformativeText("\n".join(linhas))
        m.exec()
        self._flash("● diagnostics: " + ("all ok" if res.get("all_ok") else "see details"))
        self.refresh()

    # ---------- acoes (fase 3/4) ----------
    def _sync_actions(self, on: bool):
        vm = self._vm_running
        if hasattr(self, "actConnect"):
            self.actConnect.setEnabled(vm and not on and not self._busy)
            self.actDisconnect.setEnabled(vm and on and not self._busy)
            self.actLeave.setEnabled(vm and not self._busy)
        if hasattr(self, "actVmOn"):
            self.actVmOn.setEnabled(not vm and not self._busy)
            self.actVmOff.setEnabled(vm and not self._busy)
        if hasattr(self, "actGui"):
            self.actGui.setEnabled(vm)

    def _run_action(self, fn, label):
        if self._busy:
            return
        self._busy = True
        self._flash(f"● {label}…")
        self._sync_actions(self._last_service == "Running")
        self._action = ActionWorker(fn)
        self._action.done.connect(lambda ok, log: self._action_done(ok, log, label))
        self._start(self._action)

    def _action_done(self, ok, log, label):
        if self._closing:
            return
        self._busy = False
        if ok:
            self._flash(f"● {label}: ok")
        else:
            self._flash(f"● {label}: failed")
        QTimer.singleShot(1500, self.refresh)

    def toggle_power(self):
        # the UI power button turns the WHOLE VM on/off (Radmin Linux = the VM)
        if self._busy:
            return
        if self._vm_running:
            self.do_vm_off()
        else:
            self.do_vm_on()

    # ---------- whole-VM control ----------
    def do_vm_on(self):
        if self._busy or self._vm_running:
            return
        self._busy = True
        self._vm_started_at = time.monotonic()   # start the watchdog grace now
        self._flash("● turning VM on…")
        self.nodeName.setText("starting…")
        self._sync_actions(False)
        self._agent_checked = False
        self._action = ActionWorker(lambda: (vmctl.power_on(), "on"))
        self._action.done.connect(lambda ok, log: self._vm_action_done("Turning on"))
        self._start(self._action)

    def do_vm_off(self):
        from PySide6.QtWidgets import QMessageBox
        if self._busy or not self._vm_running:
            return
        r = QMessageBox.question(self, "Turn off Radmin VPN",
            "This fully shuts down the VM.\nRadmin and all peers will be unavailable until you turn it back on.\n\nTurn off?")
        if r != QMessageBox.Yes:
            return
        self._busy = True
        self._user_stopped = True
        self._flash("● turning VM off…")
        self._sync_actions(True)
        self._action = ActionWorker(lambda: (vmctl.power_off(), "off"))
        self._action.done.connect(lambda ok, log: self._vm_action_done("Turning off"))
        self._start(self._action)

    def _vm_action_done(self, label):
        if self._closing:
            return
        self._busy = False
        self._flash(f"● {label}: ok")
        self._sync_actions(False)
        # fire one early refresh; while the VM boots _apply keeps polling the API
        QTimer.singleShot(4000, self.refresh)

    # ---------- agent / automation ----------
    def do_check_update(self):
        if self._busy:
            return
        self._busy = True
        self._flash("● checking for update…")
        self._sync_actions(self._last_service == "Running")
        self._action = ActionWorker(lambda: (agent.check_update(install=False), ""))
        self._action.done.connect(self._update_checked)
        self._start(self._action)

    def _update_checked(self, res, _log):
        from PySide6.QtWidgets import QMessageBox
        self._busy = False
        self._sync_actions(self._last_service == "Running")
        if not isinstance(res, dict) or res.get("error"):
            self._flash("● update: error")
            err = res.get("error") if isinstance(res, dict) else res
            QMessageBox.warning(self, "Update", f"Could not check:\n{err}")
            return
        cur = res.get("installed_version", "?")
        lat = res.get("latest_version", "?")
        if res.get("blocked"):
            self._flash(f"● Radmin {cur} ({lat} needs a Windows update)")
            QMessageBox.information(self, "Update", res["blocked"])
            return
        if res.get("has_update"):
            r = QMessageBox.question(self, "Update available",
                f"Installed: {cur}\nAvailable: {lat}\n\nInstall now on the VM?")
            if r == QMessageBox.Yes:
                def _inst():
                    r = agent.check_update(install=True)
                    return bool(r.get("did_install")), str(r.get("error", ""))
                self._run_action(_inst, "Updating Radmin")
            else:
                self._flash(f"● {cur} ({lat} available)")
        else:
            self._flash(f"● Radmin up to date ({cur})")
            QMessageBox.information(self, "Update", f"Already on the latest version ({cur}).")

    def do_rename_node(self):
        from PySide6.QtWidgets import QInputDialog
        if self._busy or not self._vm_running:
            return
        cur = self.nodeName.text()
        new, ok = QInputDialog.getText(self, "Rename node",
            "New name for this node (what other peers see):", text=cur)
        new = (new or "").strip()
        if ok and new and new != cur:
            self._run_action(lambda: actions.rename_node(new), "Renaming node")

    def do_orchestrate(self):
        self._run_action(lambda: agent.orchestrate_net(), "Repairing network")

    def do_install_agent(self):
        if not self._vm_running:
            return
        self._run_action(lambda: (agent.ensure_agent(force=True, log=lambda m: None) != "failed", ""),
                         "Reinstalling the VM agent")

    def do_reset_identity(self):
        from PySide6.QtWidgets import QMessageBox
        if self._busy or not self._vm_running:
            return
        r = QMessageBox.question(self, "Reset Radmin identity",
            "Give this VM a brand new Radmin VPN identity?\n\n"
            "You get a new node and a new 26.x address and leave ALL networks. "
            "Peers will see you as a new member, and you must join your networks again.")
        if r == QMessageBox.Yes:
            self._identity_seen = ""
            self._run_action(actions.reset_identity, "Resetting identity")

    def do_connect(self):
        self._run_action(actions.connect, "Connecting")

    def do_disconnect(self):
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(self, "Disconnect",
            "Disconnect from Radmin VPN?\nPeers will be unreachable until you reconnect.")
        if r == QMessageBox.Yes:
            self._user_stopped = True
            self._run_action(actions.disconnect, "Disconnecting")

    def do_leave(self):
        from PySide6.QtWidgets import QMessageBox
        guids = backend.list_networks()
        if not guids:
            QMessageBox.information(self, "Leave network", "No associated network found.")
            return
        # if more than one, ask which
        labels = [f"{self.roster.net_label(g)}  {g}" for g in guids]
        if len(guids) == 1:
            guid = guids[0]
        else:
            pick, ok = QInputDialog.getItem(self, "Leave network",
                "Which network to leave? (names are shown in the Radmin window)", labels, 0, False)
            if not ok:
                return
            guid = guids[labels.index(pick)]
        r = QMessageBox.question(self, "Leave network",
            f"Leave {self.roster.net_label(guid)}\n{guid}?\n\nYou will need to join again with name and password.")
        if r == QMessageBox.Yes:
            self._run_action(lambda: actions.leave_network(guid), "Leaving network")

    def do_join(self):
        self._open_gui_for("join a network")

    def do_create(self):
        self._open_gui_for("create a network")

    def _open_gui_for(self, action):
        from PySide6.QtWidgets import QMessageBox
        m = QMessageBox(self)
        m.setWindowTitle("Radmin VPN")
        m.setIcon(QMessageBox.Information)
        m.setText(f"To {action} you need the real Radmin window.")
        m.setInformativeText(
            "This does a handshake with the Radmin server (network name + password) "
            "that only the official GUI performs — it cannot be done via the registry.\n\n"
            "I can open the VM's Radmin window here (VNC). Do it there and "
            "this list updates on its own afterwards.")
        b_open = m.addButton("Open Radmin window (VNC)", QMessageBox.AcceptRole)
        m.addButton("Cancel", QMessageBox.RejectRole)
        m.exec()
        if m.clickedButton() is b_open:
            self._open_vnc()

    def _open_vnc(self):
        """Bring the Radmin window to the front inside the VM, then open the viewer."""
        import subprocess
        if not self._vm_running:
            self._flash("● turn the VM on first")
            return
        view = config.VIEW_SCRIPT
        if not os.path.exists(view):
            self._flash(f"● viewer script not found: {view}")
            return
        def _go():
            actions.show_gui()
            subprocess.Popen(["bash", view], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True, ""
        w = ActionWorker(_go)
        w.done.connect(lambda ok, log: self._flash("● Radmin window opened"))
        self._start(w)
        self._flash("● opening the Radmin window…")
        QTimer.singleShot(20000, self.refresh)

    # ---------- app updates ----------
    def check_app_update(self, manual=False):
        if self._closing or (self._upd and self._upd.isRunning()):
            return
        if not manual:
            if not self.roster.get_setting("auto_update_check", True):
                return
            if time.time() - float(self.roster.get_setting("last_update_check", 0) or 0) < UPDATE_CHECK_S:
                return
        else:
            self._flash("● checking for app updates…")
        self._upd = ActionWorker(lambda: (updater.check(), ""))
        self._upd.done.connect(lambda res, _l: self._app_update_checked(res, manual))
        self._start(self._upd)

    def _app_update_checked(self, res, manual):
        if self._closing:
            return
        if not res.get("ok"):
            if manual:
                QMessageBox.warning(self, "App update", f"Could not check: {res.get('error')}")
            return
        self.roster.set_setting("last_update_check", int(time.time()))
        if not res.get("newer"):
            if manual:
                QMessageBox.information(self, "App update",
                    f"Radmin-Linux {config.VERSION} is the latest version.")
            return
        self._update_offer = res
        if not manual:
            self._notify("Radmin-Linux update", f"Version {res['latest']} is available (Help > Check for app updates).")
            self._flash(f"● app update {res['latest']} available (Help menu)", 15)
            return
        m = QMessageBox(self)
        m.setWindowTitle("App update")
        m.setIcon(QMessageBox.Information)
        m.setText(f"Radmin-Linux {res['latest']} is available (you have {config.VERSION}).")
        m.setInformativeText("The update downloads only the app (your VM and your Radmin "
                             "identity stay). It may ask for your password if a system "
                             "package is missing.")
        m.setDetailedText(updater.short_notes(res.get("notes", "")))
        if updater.managed_by_package():
            m.setInformativeText("This copy was installed from a package: update it with "
                                 "your package manager (or the release page).")
            m.addButton("OK", QMessageBox.AcceptRole)
            m.exec()
            return
        b_now = m.addButton("Update now", QMessageBox.AcceptRole)
        m.addButton("Later", QMessageBox.RejectRole)
        m.exec()
        if m.clickedButton() is b_now:
            self._install_app_update(res)

    def _install_app_update(self, res):
        if self._busy:
            return
        self._busy = True
        self._flash(f"● updating the app to {res['latest']}… (log: install.log)", 3600)
        self._sync_actions(self._last_service == "Running")
        w = ActionWorker(lambda: updater.install(res["tag"], res["mirror"]))
        w.done.connect(lambda ok, log: self._app_update_done(ok, log, res))
        self._start(w)

    def _app_update_done(self, ok, log, res):
        self._busy = False
        self._flash_until = 0
        self._sync_actions(self._last_service == "Running")
        if not ok:
            m = QMessageBox(self); m.setIcon(QMessageBox.Warning)
            m.setWindowTitle("App update"); m.setText("The update failed. Nothing was removed.")
            m.setDetailedText(log); m.exec()
            self._flash("● app update failed (Help > Open the install log)", 15)
            return
        r = QMessageBox.question(self, "App update",
            f"Updated to {res['latest']}. Restart the app now?\n(The VM keeps running.)")
        if r == QMessageBox.Yes:
            updater.relaunch()
            QApplication.quit()
        else:
            self._flash(f"● updated to {res['latest']}, restart the app to use it", 30)

    def _quit(self):
        if any(isinstance(t, ActionWorker) and t.isRunning() and t is not self._upd
               for t in self._threads) and self._busy:
            r = QMessageBox.question(self, "Quit",
                "An operation (update or VM action) is still running. Quit anyway?")
            if r != QMessageBox.Yes:
                return
        QApplication.quit()

    def _toggle_autostart(self, on):
        set_autostart(bool(on))
        self._flash("● starts with your session" if on else "● no longer starts with your session")

    def _open_path(self, path):
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self._flash(f"● not found: {path}")

    # ---------- misc ----------
    def _about(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(self, "About",
            f"Radmin VPN (Linux) {config.VERSION}\n"
            f"VM agent {config.agent_version()}\n\n"
            "Drives the real Radmin VPN running headless in a small\n"
            "Windows VM. The power button turns the whole VM on/off;\n"
            "peers come live from the mesh; the VM self-heals in the background.\n"
            "Active peers via ARP/ping; nicknames are local.\n\n"
            f"Footprint: {config.vm_footprint()}.\n\n"
            "Not affiliated with Famatech. Radmin VPN is their product.")

    def _show_raise(self):
        self.showNormal(); self.raise_(); self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._show_raise()

    def closeEvent(self, e):
        # close to the tray instead of quitting. Without a tray (some desktops)
        # closing quits, otherwise the app would live on with no window at all.
        if self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            e.ignore(); self.hide()
            if not self.roster.get_setting("tray_hint_shown", False):
                self.roster.set_setting("tray_hint_shown", True)
                self.tray.showMessage("Radmin VPN", "Still running in the tray. Quit from the tray menu.",
                                      icons.logo_icon(64), 5000)
        else:
            QApplication.quit()

    def shutdown(self):
        self._closing = True
        self.roster.flush()
        self.timer.stop()
        self.pingtimer.stop()
        self.healthtimer.stop()
        # 1) cancel and KILL every tracked subprocess (wmiexec/ping/dump/agent):
        #    this immediately unblocks the worker threads stuck in communicate()/ping,
        #    which then return on their own.
        import backend
        backend.cancel_all()
        # 2) wait for each worker to finish. With the children killed, wait() returns
        #    quickly. NO terminate(): killing a QThread mid-Python is what made Qt
        #    abort (qFatal) during finalize. The QThreads are not Qt children of the
        #    window; that is why we track them in self._threads (findChildren missed them).
        for t in list(self._threads):
            try:
                if t.isRunning():
                    t.wait(5000)
            except RuntimeError:
                pass


AUTOSTART = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                         "autostart", "radmin-linux.desktop")


def autostart_enabled() -> bool:
    return os.path.exists(AUTOSTART)


def set_autostart(on: bool) -> None:
    if not on:
        try:
            os.remove(AUTOSTART)
        except OSError:
            pass
        return
    launcher = os.path.join(config.HOME, "radmin-linux.sh")
    if not os.path.exists(launcher):
        launcher = str(config.CODE_ROOT / "radmin-linux.sh")
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Radmin VPN (Unofficial)\n"
                f"Exec={launcher} --hidden\nIcon=radmin-linux\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\nX-KDE-autostart-after=panel\n")


INSTANCE = f"radmin-linux-{os.getuid()}"


def _already_running() -> bool:
    """Single instance: a second launch asks the first one to show its window."""
    s = QLocalSocket()
    s.connectToServer(INSTANCE)
    if s.waitForConnected(500):
        s.write(b"show\n"); s.flush(); s.waitForBytesWritten(500)
        s.disconnectFromServer()
        return True
    return False


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Radmin VPN (Linux)")
    app.setDesktopFileName("radmin-linux")
    app.setQuitOnLastWindowClosed(False)
    if _already_running():
        return 0
    QLocalServer.removeServer(INSTANCE)     # stale socket of a crashed instance
    server = QLocalServer()
    server.listen(INSTANCE)
    w = MainWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), w._show_raise()))
    hidden = "--hidden" in sys.argv and QSystemTrayIcon.isSystemTrayAvailable()
    if not hidden:
        w.show()
    app.aboutToQuit.connect(w.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
