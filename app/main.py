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
    QSizePolicy, QPushButton, QMessageBox, QLineEdit, QSizeGrip, QComboBox, QDialog,
)
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QEvent
from PySide6.QtNetwork import QLocalServer, QLocalSocket

sys.path.insert(0, os.path.dirname(__file__))
import backend
import actions
import agent
import vmctl
import icons
import radui
import config
import orctl
if config.NATIVE:
    # native engine: OpenRad has the same control functions as the VM
    # (is_running, power_on, power_off, ...), so the window drives either one
    vmctl = orctl  # noqa: F811
import updater
from roster import Roster

NATIVE = config.NATIVE
# words that differ between the two engines
WHAT = "Radmin" if NATIVE else "VM"            # the thing the power button turns on
OFF_TEXT = "Radmin is off" if NATIVE else "VM is off"
BOOT_TEXT = "connecting to the Radmin server" if NATIVE else "VM is booting, please wait"

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
        self.done.emit(orctl.diagnostics() if NATIVE else agent.health(heal=self.heal))


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


def _api_num(v) -> int:
    """Agent API version as a number ("" or "?" = 0: WMI only or unknown)."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


class NetHeader(QFrame):
    """Radmin's network pill: ▼ name, click folds it, right click manages it."""
    def __init__(self, gid, name, count, collapsed, on_toggle, on_menu):
        super().__init__()
        self.gid = gid
        self.setObjectName("netHeader")
        self.setFixedHeight(24)
        lay = QHBoxLayout(self); lay.setContentsMargins(9, 0, 10, 0); lay.setSpacing(6)
        self.arrow = QLabel(); self.arrow.setStyleSheet("color:#ffffff; font-size:10px;")
        lay.addWidget(self.arrow)
        self.nm = QLabel(); self.nm.setProperty("class", "netHeaderName")
        lay.addWidget(self.nm, 1)
        self.cnt = QLabel(); self.cnt.setProperty("class", "netHeaderCount")
        lay.addWidget(self.cnt)
        self._on_toggle = on_toggle
        self._on_menu = on_menu
        self.update_info(name, count, collapsed)

    def update_info(self, name, count, collapsed):
        self.nm.setText(name)
        self.cnt.setText("")          # Radmin shows no counter here (tooltip has it)
        self.setToolTip(f"{name} · {count} online")
        self.arrow.setText("▶" if collapsed else "▼")

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton and self._on_menu:
            self._on_menu(self.gid, e.globalPosition().toPoint())
        elif e.button() == Qt.LeftButton:
            self._on_toggle(self.gid)


def _repolish(w):
    st = w.style(); st.unpolish(w); st.polish(w)


class PeerRow(QFrame):
    """One member, as in Radmin: signal bars, name, IP on the right. Rows are
    reused across refreshes: set_state() only touches what changed."""
    def __init__(self, ip, on_rename, on_menu=None, key=None, on_select=None):
        super().__init__()
        self.ip = ip
        self.key = key or ip      # the same peer can be in two networks
        self._on_menu = on_menu
        self._on_rename = on_rename
        self._on_select = on_select
        self.setObjectName("peerRow")
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedHeight(22)
        self._online = None
        self._label = None
        self._rtt = None
        self._via = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(5)
        self.sig = QLabel(); self.sig.setFixedWidth(20)
        lay.addWidget(self.sig)
        self.nameLbl = QLabel(); self.nameLbl.setProperty("class", "peerName")
        lay.addWidget(self.nameLbl, 1)
        self.rttLbl = QLabel()       # kept for the code that reads it; Radmin shows no ping here
        self.rttLbl.hide()
        self.ipLbl = QLabel(ip); self.ipLbl.setProperty("class", "peerIp")
        self.ipLbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.ipLbl)

    def set_state(self, label, online, rtt=None, via=""):
        if online != self._online:
            self._online = online
            self.sig.setPixmap(icons.signal_pixmap(online, 16))
            self.nameLbl.setProperty("off", not online)
            _repolish(self.nameLbl)
        if label != self._label:
            self._label = label
            self.nameLbl.setText(label)
        rtt = rtt if online else None
        via = via if online else ""
        if rtt != self._rtt or via != self._via or self.toolTip() == "":
            self._rtt = rtt
            self._via = via
            how = {"udp": "UDP", "tcp": "TCP", "tcprelay": "TCP relay"}.get(via.lower(), "")
            tip = [f"{label}", f"IPv4: {self.ip}", "Status: " + ("Online" if online else "Offline")]
            if how:
                tip.append(f"Channel type: {how}")
            if rtt is not None:
                tip.append(f"Ping: {rtt:.0f} ms" if rtt >= 1 else "Ping: <1 ms")
            self.setToolTip("\n".join(tip))

    def set_selected(self, on):
        if bool(self.property("sel")) != on:
            self.setProperty("sel", on)
            _repolish(self)

    def mousePressEvent(self, e):
        if self._on_select:
            self._on_select(self.key)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self._on_rename(self.ip, self.key)

    def contextMenuEvent(self, e):
        if self._on_select:
            self._on_select(self.key)
        if self._on_menu:
            self._on_menu(self.ip, e.globalPos(), self.key)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.roster = Roster()
        self.setWindowTitle("Radmin VPN")
        self.setWindowIcon(icons.logo_icon(64))
        self.resize(330, 540)
        self.setMinimumSize(300, 330)
        self._sel_key = None
        self._fetcher = None
        self._pinger = None
        self._action = None
        self._threads = set()   # all live QThreads, for shutdown to wait on
        self._busy = False
        self._booting = False   # VM on but WMI not answering yet (booting)
        self._stopping = False  # a shutdown is in progress (vmctl.is_stopping)
        self._quitting = False  # Quit: turning the VM off, then the app exits
        self._viewers = []      # VNC viewers opened by the app (closed on Quit)
        self._keep_vm = False   # app restart after an update: leave the VM on
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
        # What the list shows:
        #   "live"     the networks exactly as the Radmin window shows them (agent 5+)
        #   "fallback" ARP + roster, only when the VM agent is old and could not be updated
        #   None       nothing yet: the reading is not finished (no stale peers, no
        #              old groups, no guessed networks on screen meanwhile)
        self._mode = None
        self._waiting = "reading the Radmin window"
        self._agent_failed = False
        self._live = None          # networks as the Radmin window shows them (agent 5+)
        self._live_online = set()  # members Radmin is connected to (service.log)
        self._live_via = {}        # ip -> UDP / TCP / TcpRelay
        self._live_meta = {}       # (network name, ip) -> {"rid", "role"} (native)
        self._blocked = {}         # ip -> both / incoming / outgoing (native)
        self._force_relay = False  # native: relays only
        self._hidden_rows = 0
        self._n_online = 0
        self._summary = ""
        self._flash_until = 0.0
        self._user_stopped = False   # the user disconnected / turned off: no alert
        self._was_online = False
        self._upd = None
        self._update_offer = None
        self._build()
        self.actSync.setVisible(False)          # only for old agents (fallback)
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
        self._show_identity()
        self._set_badge("Manual" if running else "Offline", "warn" if running else "off")
        self.power.set_state("on" if running else "off")
        self._sync_actions(False)
        self._update_tray()
        # nothing is listed until a query has read the Radmin window
        self._set_mode(None)
        self._waiting = "manual mode, Refresh to query"
        self._render_peers(set())
        self._set_summary("● manual mode, Refresh to query" + ("" if running else f" · {OFF_TEXT}"))

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
    def _act(self, text, slot=None, checkable=False, checked=False, tip="", shortcut=None):
        a = QAction(text, self)
        if checkable:
            a.setCheckable(True); a.setChecked(bool(checked))
        if slot is not None:
            (a.toggled if checkable else a.triggered).connect(slot)
        if tip:
            a.setToolTip(tip)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        return a

    def _build(self):
        """Radmin VPN's window: frameless title, System / Network / Help, the node
        panel (power button, name, IP, Online badge, orange notice bar) and the
        network panel. Linux only extras live in System > Settings."""
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        root = QWidget(); root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(radui.TitleBar(self, self.close, self.showMinimized))

        # every action the app has; the menus show Radmin's, Settings the rest
        self.actAuto = self._act("Auto-refresh", self._toggle_auto, True, self._auto,
                                 "Refresh the list every few seconds")
        self.actVmOn = self._act("Turn on" if NATIVE else "Turn VM on", self.do_vm_on)
        self.actVmOff = self._act("Turn off" if NATIVE else "Turn VM off", self.do_vm_off)
        self.actResetId = self._act("Reset Radmin identity…", self.do_reset_identity)
        self.actAutostart = self._act("Start with my session (in the tray)", self._toggle_autostart,
                                      True, autostart_enabled())
        self.actVmOnStart = self._act("Connect when the app opens" if NATIVE else "Turn the VM on when the app opens",
                                      lambda on: self.roster.set_setting("vm_on_start", bool(on)), True,
                                      self.roster.get_setting("vm_on_start", False))
        self.actNotify = self._act("Desktop notifications",
                                   lambda on: self.roster.set_setting("notify", bool(on)), True,
                                   self.roster.get_setting("notify", True))
        self.actAutoUpd = self._act("Automatic updates",
                                    lambda on: self.roster.set_setting("auto_update_check", bool(on)), True,
                                    self.roster.get_setting("auto_update_check", True))
        self.actOnlineOnly = self._act("Show online only", self._toggle_online_only, True,
                                       self.roster.get_setting("online_only", False))
        self.actConnect = self._act("Go Online", self.do_connect)
        self.actDisconnect = self._act("Go Offline", self.do_disconnect)
        self.actRename = self._act("Change Name", self.do_rename_inline)
        self.actSync = self._act("Sync network members", self.discover_now)
        self.actLeave = self._act("Leave Network", self.do_leave)
        self.actJoin = self._act("Join Network", self.do_join, shortcut="+")
        self.actCreate = self._act("Create Network", self.do_create, shortcut="Ins")
        self.actGui = self._act("Open the Radmin window", self._open_vnc)
        if NATIVE:
            self.actForceRelay = self._act("Use relays only", self._toggle_force_relay, True, False)
        self.actRefresh = self._act("Refresh", self.refresh_full, shortcut="F5")
        self.addAction(self.actRefresh); self.addAction(self.actJoin); self.addAction(self.actCreate)

        mb = QMenuBar(); mb.setObjectName("menubar")
        m = mb.addMenu("&System")
        m.addAction(self.actRename)
        m.addAction(self._act("Settings", self.do_settings))
        m.addSeparator()
        m.addAction(self._act("Exit", self._quit, tip="Disconnects and closes the app" if NATIVE
                              else "Turns the VM off and closes the app"))
        m = mb.addMenu("&Network")
        m.addAction(self.actCreate)
        m.addAction(self.actJoin)
        m = mb.addMenu("&Help")
        m.addAction(self._act("&Help", lambda: QDesktopServices.openUrl(QUrl(
            f"https://github.com/{updater.GITHUB}#readme")), shortcut="F1"))
        m.addAction(self._act("View &Log", self.do_view_log))
        m.addSeparator()
        m.addAction(self._act("Report a &Bug", lambda: QDesktopServices.openUrl(QUrl(
            f"https://github.com/{updater.GITHUB}/issues/new"))))
        m.addAction(self._act("Request a &Feature", lambda: QDesktopServices.openUrl(QUrl(
            f"https://github.com/{updater.GITHUB}/issues/new"))))
        m.addSeparator()
        m.addAction(self._act("Check for &Updates…", lambda: self.check_app_update(manual=True)))
        m.addSeparator()
        m.addAction(self._act("&About…", self._about))
        mbw = QWidget(); mbl = QHBoxLayout(mbw); mbl.setContentsMargins(10, 2, 10, 0)
        mbl.addWidget(mb); mbl.addStretch(1)
        mbw.setStyleSheet(f"background: {radui.BG};")
        v.addWidget(mbw)

        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(10, 6, 10, 6); bl.setSpacing(8)
        # node panel
        top = QFrame(); top.setObjectName("topPanel")
        tl = QVBoxLayout(top); tl.setContentsMargins(8, 8, 8, 8); tl.setSpacing(6)
        row = QHBoxLayout(); row.setSpacing(10)
        self.power = radui.PowerButton()
        self.power.setToolTip("Go Online / Go Offline")
        self.power.clicked.connect(self.toggle_power)
        row.addWidget(self.power)
        box = QVBoxLayout(); box.setSpacing(3)
        self.nodeName = QLabel("—"); self.nodeName.setObjectName("nodeName")
        self.nodeName.setToolTip("Double-click to change the name")
        self.nodeName.mouseDoubleClickEvent = lambda e: self.do_rename_inline()
        self.nameEdit = QLineEdit(); self.nameEdit.setObjectName("nodeNameEdit"); self.nameEdit.hide()
        self.nameEdit.returnPressed.connect(self._rename_commit)
        self.nameEdit.editingFinished.connect(self._rename_cancel)
        self.nameEdit.installEventFilter(self)
        self.nodeIp = QLabel("—"); self.nodeIp.setObjectName("nodeIp")
        self.nodeIp.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rowb = QHBoxLayout(); rowb.setSpacing(6)
        self.badge = QLabel("Offline"); self.badge.setObjectName("badge")
        self.badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        rowb.addWidget(self.badge); rowb.addStretch(1)
        box.addStretch(1)
        box.addWidget(self.nodeName); box.addWidget(self.nameEdit); box.addWidget(self.nodeIp)
        box.addLayout(rowb); box.addStretch(1)
        row.addLayout(box, 1)
        tl.addLayout(row)
        self.status = QLabel(""); self.status.setObjectName("notice")
        self.status.setAlignment(Qt.AlignCenter); self.status.setWordWrap(True)
        self.status.setCursor(QCursor(Qt.PointingHandCursor))
        self.status.mousePressEvent = lambda e: self._notice_clicked()
        self.status.hide()
        tl.addWidget(self.status)
        bl.addWidget(top)

        # network panel (sized to its content, like Radmin's)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.listw = QWidget(); self.listw.setObjectName("list")
        outer = QVBoxLayout(self.listw); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(6)
        self.netPanel = QFrame(); self.netPanel.setObjectName("netPanel")
        self.listv = QVBoxLayout(self.netPanel)
        self.listv.setContentsMargins(4, 4, 4, 6); self.listv.setSpacing(1)
        self.listv.addStretch(0)
        self.netPanel.hide()
        outer.addWidget(self.netPanel)
        self.hint = QLabel(""); self.hint.setObjectName("hint"); self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)
        outer.addStretch(1)
        self.scroll.setWidget(self.listw)
        self.scroll.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll.customContextMenuRequested.connect(
            lambda pos: self._empty_menu(self.scroll.mapToGlobal(pos)))
        bl.addWidget(self.scroll, 1)
        v.addWidget(body, 1)
        grip = QHBoxLayout(); grip.setContentsMargins(0, 0, 0, 0); grip.addStretch(1)
        grip.addWidget(QSizeGrip(root))
        v.addLayout(grip)

        self.setStyleSheet(radui.QSS)
        self._build_tray()

    def _build_tray(self):
        """Radmin's tray menu: Open, Go Online / Go Offline, Exit."""
        self.tray = QSystemTrayIcon(icons.logo_icon(64), self)
        self.tray.setToolTip("Radmin VPN")
        menu = QMenu()
        menu.setStyleSheet(radui.QSS)
        show = QAction("Open", self); show.triggered.connect(self._show_raise)
        f = show.font(); f.setBold(True); show.setFont(f)
        menu.addAction(show)
        self.trayShow = show
        self.trayPower = QAction("Go Online", self)
        self.trayPower.triggered.connect(self.toggle_power); menu.addAction(self.trayPower)
        self.trayConn = QAction("Connect / Disconnect", self)   # VM: Radmin service only
        self.trayConn.triggered.connect(self._tray_toggle_conn)
        menu.addSeparator()
        quit_ = QAction("Exit", self); quit_.triggered.connect(self._quit); menu.addAction(quit_)
        quit_.setToolTip("Disconnects and closes the app" if NATIVE else "Turns the VM off and closes the app")
        self.trayQuit = quit_
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
        if self._quitting:
            tip, state = f"Radmin VPN · turning {'Radmin' if NATIVE else 'the VM'} off, then quitting", "warn"
        elif not self._vm_running:
            tip, state = f"Radmin VPN · {'off' if NATIVE else 'VM off'}", "off"
        elif self._stopping:
            tip, state = "Radmin VPN · shutting down", "warn"
        elif self._booting:
            tip, state = "Radmin VPN · " + ("connecting" if NATIVE else "starting"), "warn"
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
            self.trayConn.setEnabled(self._vm_running and not self._booting and not self._stopping)
        if hasattr(self, "trayPower"):
            self.trayPower.setText("Go Offline" if self._vm_running else "Go Online")
            self.trayPower.setEnabled(not self._stopping)

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
        if self._closing or self._quitting:
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
        if self._agent_checked or self._closing or NATIVE:
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
        self._agent_failed = (res == "failed")
        self._flash({"ok": "● VM agent up to date", "upgraded": "● VM agent updated",
                             "failed": "● could not update the VM agent (System > Reinstall)"}.get(res, res))
        QTimer.singleShot(1500, self.refresh)

    def _check_identity_notice(self, st):
        note = st.identity_reset
        if NATIVE or not note or note == self._identity_seen or self._closing:
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
        if self._closing or self._quitting:
            return
        if st.ok and self._stopping and self._busy:
            return   # read just before the shutdown request: the VM is going away
        was_running = self._vm_running
        self._vm_running = st.vm_running
        if st.vm_running and not was_running:
            # VM just came up (or app opened while it was already booting): start the
            # watchdog grace so a slow boot is never mistaken for a hang.
            self._vm_started_at = time.monotonic()
        if not st.ok:
            self._last_service = "Stopped"
            self._live, self._live_online, self._live_via = None, set(), {}
            self._set_mode(None)
            # qemu alive but the agent not answering = VM BOOTING, unless a
            # shutdown is in progress (then it is going away, not coming up)
            was_stopping = self._stopping
            self._stopping = bool(st.vm_running and st.stopping)
            self._booting = bool(st.vm_running) and not self._stopping
            if not st.vm_running:
                self._agent_checked = False
                self._show_identity()
                self._set_badge("Offline", "off")
                self.power.set_state("off")
                # no alert when it went down on request (app, tray or bench-stop.sh)
                if was_running and not self._user_stopped and not self._busy and not was_stopping:
                    self._notify("Radmin VPN stopped", "OpenRad is no longer running." if NATIVE
                                 else "The Windows VM is no longer running.")
                self._was_online = False
            elif self._stopping:
                self._show_stopping()
            else:
                self._show_identity(BOOT_TEXT)
                self._set_badge("Connecting" if NATIVE else "Starting", "warn")
                self.power.set_state("busy")
            self._set_summary(f"● {st.error}")
            self._sync_actions(False)
            self._update_tray()
            self._clear_list()
            # while booting or shutting down, re-check often so the switch to
            # online / off feels live. Cheap (one HTTP connect to the VM, or a
            # file read while stopping), so it runs in manual mode too.
            if self._stopping and not self._closing:
                QTimer.singleShot(BOOT_POLL_MS, self.refresh)
            if self._booting and not self._closing:
                QTimer.singleShot(BOOT_POLL_MS, self.refresh)
                # an old agent never brings the API up: upgrade it over WMI
                if not NATIVE and time.monotonic() - self._vm_started_at > 180:
                    self._maybe_ensure_agent(st)
            return

        first = self._booting or not was_running or self._last_service != "Running"
        self._booting = False
        self._stopping = False
        self._maybe_ensure_agent(st)
        self._check_identity_notice(st)
        self._last_service = st.service
        # the name peers see is Radmin's Alias, not the Windows hostname
        self.nodeName.setText(st.alias or st.hostname or "—")
        self.nodeIp.setText(st.node_ip or "—")
        self._node_ip = st.node_ip
        on = (st.service == "Running")
        if self._was_online and not on and not self._user_stopped and not self._busy:
            self._notify("Radmin VPN disconnected", "OpenRad lost the Radmin server." if NATIVE
                         else "The Radmin service stopped in the VM.")
        if on:
            self._user_stopped = False
        self._was_online = on
        self.power.set_state("on" if on else "off")
        self._set_badge("Online" if on else "Offline", "on" if on else "off")
        self._sync_actions(on)

        # agent 5+: the real member list, read from the Radmin window. Until
        # that reading is complete the list stays empty. ARP and the GUI memory
        # dump are only the fallback when the VM agent could not be updated.
        live = self._take_live(st.radmin)
        if live is not None or (_api_num(st.api) >= 5 and self._live is not None):
            self._set_mode("live")         # a short reader hiccup keeps the last list
        elif self._agent_failed:
            self._set_mode("fallback")
        else:
            self._set_mode(None)
            self._waiting = (st.radmin or {}).get("error") or (
                "updating the VM agent" if _api_num(st.api) < 5 else "reading the Radmin window")
        new_peer = False
        if live is not None:
            for net in live:
                for m in net["members"]:
                    new_peer |= self.roster.seen(m["ip"], "", m["name"])
        elif self._mode == "fallback":
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
        if self._mode == "live":
            self._render_peers(self._live_online | self._ping_online)
        elif self._mode is None:
            self._render_peers(set())
        else:
            arp = {p.ip for p in st.peers if p.ip != "26.0.0.1"}
            self._render_peers(self._ping_online | {ip for ip in arp if ip not in self._pinged})
        if not on:
            self._set_summary("● Radmin is disconnected (Network > Connect)")
        if NATIVE:
            self._native_warning(st)
        if first or new_peer:
            self.ping_now()

        # first discovery only now, with the VM confirmed on and the service up
        if getattr(self, "_want_discover", False) and on and self._mode == "fallback":
            self._want_discover = False
            QTimer.singleShot(3000, self.discover_now)

    def _take_live(self, r):
        """Keep the agent's view of the Radmin window. Returns the networks or
        None (old agent / reader not running: the ARP + roster fallback)."""
        if not isinstance(r, dict) or not r.get("ok") or not isinstance(r.get("networks"), list):
            return None
        status = str(r.get("status") or "")
        if not r["networks"] and status.lower() != "online":
            # Radmin still connecting: an empty list now would be a guess
            self._waiting = f"Radmin is {status.lower() or 'starting'}"
            return None
        nets, online, via, meta = [], set(), {}, {}
        for n in r["networks"]:
            if not isinstance(n, dict):
                continue
            ms = []
            for m in n.get("members") or []:
                ip = str(m.get("ip") or "")
                if not ip.startswith("26.") or ip == self._node_ip:
                    continue
                ms.append({"ip": ip, "name": str(m.get("name") or "")})
                meta[(str(n.get("name") or "?"), ip)] = {"rid": m.get("rid"), "role": m.get("role")}
                if m.get("online"):
                    online.add(ip)
                    via[ip] = str(m.get("via") or "")
            nets.append({"name": str(n.get("name") or "?"), "members": ms,
                         "id": n.get("id"), "role": n.get("role")})
        self._live, self._live_online, self._live_via = nets, online, via
        self._live_meta = meta
        self._blocked = r.get("blocked") if isinstance(r.get("blocked"), dict) else {}
        self._force_relay = bool(r.get("force_relay"))
        if hasattr(self, "actForceRelay") and self.actForceRelay.isChecked() != self._force_relay:
            self.actForceRelay.blockSignals(True)
            self.actForceRelay.setChecked(self._force_relay)
            self.actForceRelay.blockSignals(False)
        return nets

    def _set_mode(self, mode):
        if mode == self._mode:
            return
        self._mode = mode
        self._place([])            # start the list from scratch
        self._layout_sig = None
        fb = mode == "fallback"
        self.actSync.setVisible(fb)   # the GUI dump only makes sense for old agents

    def _peer_ips(self):
        """IPs the list shows: Radmin's members (live), the local roster
        (fallback) or nothing while the reading is not done."""
        if self._mode == "live" and self._live is not None:
            return sorted({m["ip"] for n in self._live for m in n["members"]})
        if self._mode != "fallback":
            return []
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
        self.netPanel.setVisible(bool(widgets))

    def _row(self, ip, section=None):
        key = f"{section}|{ip}" if section else ip
        r = self._rows.get(key)
        if r is None:
            r = self._rows[key] = PeerRow(ip, self._row_double, self._peer_menu, key, self._select_row)
        label = self.roster.label_of(ip)
        if ip in self._blocked:
            label += "  (blocked)"
        r.set_state(label, ip in self._online, self._rtt.get(ip), self._live_via.get(ip, ""))
        r.set_selected(key == self._sel_key)
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
        if self._mode == "live" and self._live is not None:
            return self._rebuild_live()
        if self._mode != "fallback":
            self._place([])
            self._layout_sig = None
            self._hidden_rows = 0
            self._update_summary()
            return
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
            title = net["name"] + ("  (waiting for approval)" if net.get("role") == 0 else "")
            widgets.append(self._header(key, title, f"{n_on}/{len(net['members'])}", collapsed))
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
        if not self._vm_running or self._booting or self._stopping or self._last_service != "Running":
            return
        flt = " · online only" if self.roster.get_setting("online_only", False) else ""
        hidden = f" (+{self._hidden_rows} hidden)" if self._hidden_rows else ""
        if self._mode is None:
            self._set_summary(f"● {self._waiting}…")
            self._update_tray()
            return
        if self._mode == "live":
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
        """The state line. Radmin has no status bar: it goes to the tray tooltip,
        and to the hint under the list while there is nothing to list."""
        self._summary = text
        clean = text.lstrip("● ").strip()
        busy = self._vm_running and (self._mode is None or not self._live) and self._last_service == "Running"
        if not self._vm_running:
            clean = "" if (not NATIVE or orctl.identity()) else "Press the power button to create your Radmin identity"
        self.hint.setText(clean[:1].upper() + clean[1:] if (busy or not self._vm_running) and clean else "")
        self._show_notice()

    def _flash(self, text, secs=6.0):
        """A short message (action result, error) in the notice bar of the node
        panel, where Radmin shows its own notices."""
        self._notice_text = text.lstrip("● ").strip()
        self._flash_until = time.monotonic() + secs
        self._show_notice()
        QTimer.singleShot(int(secs * 1000) + 50, self._unflash)

    def _unflash(self):
        if time.monotonic() >= self._flash_until:
            self._show_notice()

    def _show_notice(self):
        """Orange bar: an action message for a few seconds, else the update offer."""
        if time.monotonic() < self._flash_until and getattr(self, "_notice_text", ""):
            t, kind = self._notice_text, "info"
        elif self._update_offer:
            t, kind = "Please update to the latest version", "update"
        else:
            self.status.hide()
            return
        t = t[:1].upper() + t[1:]
        if self.status.text() != t:
            self.status.setText(t)
        if self.status.property("kind") != kind:
            self.status.setProperty("kind", kind); _repolish(self.status)
        self.status.show()

    def _notice_clicked(self):
        if self._update_offer and time.monotonic() >= self._flash_until:
            self.check_app_update(manual=True)

    # ---------- network groups (manual assignment) ----------
    def _add_group(self):
        if self._mode != "fallback":
            # live list: the sections are Radmin's own networks
            m = QMenu(self)
            m.addAction("Join a network…", self.do_join)
            m.addAction("Create network…", self.do_create)
            m.exec(QCursor.pos())
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
            if NATIVE:
                net = self._live_net(name) or {}
                m.addAction("Leave Network", lambda: self._native_leave(name))
                d = m.addAction("Delete Network", lambda: self._native_delete(name))
                d.setEnabled(net.get("role") == 2)
                if net.get("role") != 2:
                    d.setToolTip("Only an administrator of the network can delete it")
                m.addSeparator()
            m.addAction("Copy Network Name", lambda: (QGuiApplication.clipboard().setText(name),
                                                     self._flash(f"{name} copied")))
            m.addSeparator()
            if NATIVE:
                m.addAction("Properties", lambda: self._net_properties(name))
            else:
                m.addAction("Open the Radmin window", self._open_vnc)
            m.setToolTipsVisible(True)
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

    def _peer_menu(self, ip, pos, key=None):
        m = QMenu(self)
        m.addAction("Copy IP Address", lambda: (QGuiApplication.clipboard().setText(ip),
                                               self._flash(f"{ip} copied")))
        m.addSeparator()
        m.addAction("Ping", lambda: self._ping_terminal(ip))
        if NATIVE and self._mode == "live":
            self._native_peer_items(m, ip, key)
            m.exec(pos)
            return
        if self._mode != "fallback":
            m.addSeparator()
            m.addAction("Change nickname…", lambda: self._rename(ip))
            m.exec(pos)
            return
        m.addSeparator()
        m.addAction("Change nickname…", lambda: self._rename(ip))
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

    def _empty_menu(self, pos):
        """Right click on the empty part of the list: Radmin's Create / Join."""
        if self._mode == "fallback":
            return self._add_group()
        m = QMenu(self)
        a = m.addAction("Create Network", self.do_create); a.setShortcut(QKeySequence("Ins"))
        a = m.addAction("Join Network", self.do_join); a.setShortcut(QKeySequence("+"))
        m.exec(pos)

    def _select_row(self, key):
        self._sel_key = key
        for k, r in self._rows.items():
            r.set_selected(k == key)

    def _row_double(self, ip, key=None):
        if NATIVE and self._mode == "live":
            return self._peer_properties(ip, key)
        self._rename(ip)

    def _ping_terminal(self, ip):
        """Radmin opens ping.exe in a console: here Konsole (or another terminal)
        with ping, so the user sees every reply."""
        import shutil, subprocess
        for term, args in (("konsole", ["--hold", "-e"]), ("gnome-terminal", ["--"]),
                           ("xfce4-terminal", ["-H", "-x"]), ("xterm", ["-hold", "-e"])):
            if shutil.which(term):
                try:
                    subprocess.Popen([term, *args, "ping", ip], start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    pass
        self._run_action(lambda: (lambda r: (r is not None, f"{ip}: {r} ms" if r is not None else "no reply"))(
            actions.ping_rtt([ip]).get(ip)), f"Ping {ip}")

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
        if self._closing or self._busy or self._quitting or NATIVE:
            return   # OpenRad reconnects on its own; nothing to heal from here
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
            # a VM that is shutting down is quiet on purpose: never "recover" it
            if not self._booting and not self._stopping and not in_grace and not responsive:
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
        if not self._vm_running or self._booting or self._stopping:
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
            "openrad_service": "OpenRad connected",
            "radmin_server": "Radmin server answers",
            "interface": "Network interface (radminvpn0)",
            "helper_rule": "Interface allowed without password",
        }
        linhas = []
        for c in res.get("checks", []):
            mark = "✓" if c.get("ok") else "✗"
            extra = "  (repaired)" if c.get("healed") else ""
            linhas.append(f"{mark}  {nomes.get(c['name'], c['name'])}{extra}")
        titulo = "All systems OK" if res.get("all_ok") else "Problems found"
        m = QMessageBox(self)
        m.setWindowTitle("Diagnostics" if NATIVE else "VM diagnostics")
        m.setIcon(QMessageBox.Information if res.get("all_ok") else QMessageBox.Warning)
        m.setText(titulo + (" — auto-repaired" if res.get("healed") else ""))
        if NATIVE:
            cur = res.get("current") or {}
            linhas += ["", f"OpenRad {res.get('version') or '?'}",
                       f"{cur.get('connected_count', 0)}/{cur.get('peer_count', 0)} peers connected"
                       + (f" · server {cur['latency_ms']} ms" if isinstance(cur.get("latency_ms"), int) else "")]
        m.setInformativeText("\n".join(linhas))
        m.exec()
        self._flash("● diagnostics: " + ("all ok" if res.get("all_ok") else "see details"))
        self.refresh()

    # ---------- acoes (fase 3/4) ----------
    def _sync_actions(self, on: bool):
        if self._quitting:
            return
        vm = self._vm_running and not self._stopping
        if hasattr(self, "actConnect"):
            self.actConnect.setEnabled(vm and not on and not self._busy)
            self.actDisconnect.setEnabled(vm and on and not self._busy)
            self.actLeave.setEnabled(vm and not self._busy)
        if hasattr(self, "actVmOn"):
            self.actVmOn.setEnabled(not self._vm_running and not self._busy)
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
            # OpenRad says why (wrong password, unknown network, ...): show it
            if NATIVE and isinstance(log, str) and log.strip():
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Radmin VPN", f"{label} failed.\n\n{log.strip()[-400:]}")
        QTimer.singleShot(1500, self.refresh)

    def toggle_power(self):
        # the UI power button turns the WHOLE VM on/off (Radmin Linux = the VM)
        if self._busy or self._quitting:
            return
        if self._stopping:
            self._flash("● the VM is shutting down…")
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
        self._flash("● connecting…" if NATIVE else "● turning VM on…")
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
            ("This disconnects OpenRad and removes the radminvpn0 interface." if NATIVE else
             "This fully shuts down the VM.") +
            "\nRadmin and all peers will be unavailable until you turn it back on.\n\nTurn off?")
        if r != QMessageBox.Yes:
            return
        self._busy = True
        self._user_stopped = True
        self._stopping = True
        self._booting = False
        self._show_stopping()
        self._set_summary("● disconnecting" if NATIVE else "● VM shutting down")
        self._sync_actions(False)
        self._clear_list()
        self._update_tray()
        # same as vm-stop.sh: 2 minutes for Windows to shut down, then qemu is
        # stopped (otherwise a stuck shutdown would leave the VM half off)
        self._action = ActionWorker(lambda: (
            vmctl.power_off(timeout=30 if NATIVE else 120) or (vmctl.power_off_hard() and "forced"), "off"))
        self._action.done.connect(lambda ok, log: self._vm_action_done(
            "Turning off" if ok is True else ("OpenRad did not stop, process killed" if NATIVE else
                                              "Windows did not shut down in 2 min, VM stopped"), ok))
        self._start(self._action)

    def _show_stopping(self):
        self.nodeName.setText("Disconnecting…" if NATIVE else "Shutting down…")
        self.nodeIp.setText("removing the network interface" if NATIVE else "VM is turning off, please wait")
        self._set_badge("Stopping", "warn")
        self.power.set_state("busy")

    def _vm_action_done(self, label, ok=True):
        if self._closing:
            return
        self._busy = False
        self._flash(f"● {label}: ok" if ok is True else f"● {label}")
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

    def do_rename_node(self, new=None):
        if self._busy or not self._vm_running:
            return
        cur = self.nodeName.text()
        ok = True
        if new is None:
            new, ok = QInputDialog.getText(self, "Change Name",
                "New name for this node (what other peers see):", text=cur)
        new = (new or "").strip()
        if ok and new and new != cur:
            self._run_action(lambda: (orctl.rename(new) if NATIVE else actions.rename_node(new)),
                             "Renaming node")

    # ---------- Radmin style pieces ----------
    def _show_identity(self, ip_text=None):
        """Radmin keeps the name and the 26.x address on screen while offline."""
        ident = orctl.identity() if NATIVE else {}
        self.nodeName.setText(ident.get("name") or ("Radmin VPN" if NATIVE else OFF_TEXT))
        self.nodeIp.setText(ip_text or ident.get("ip") or ("not registered yet" if NATIVE
                                                            else "click power to turn on"))

    def do_rename_inline(self):
        """System > Change Name: the name turns into a text box, like in Radmin."""
        if self._busy or not self._vm_running or self._last_service != "Running":
            self._flash("Go online first to change the name")
            return
        self._renaming = True
        self.nameEdit.setText(self.nodeName.text())
        self.nodeName.hide(); self.nameEdit.show()
        self.nameEdit.setFocus(); self.nameEdit.selectAll()

    def _rename_commit(self):
        new = self.nameEdit.text().strip()
        self._rename_cancel()
        if new:
            self.do_rename_node(new)

    def _rename_cancel(self):
        if getattr(self, "_renaming", False):
            self._renaming = False
            self.nameEdit.hide(); self.nodeName.show()

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "nameEdit", None) and ev.type() == QEvent.KeyPress \
                and ev.key() == Qt.Key_Escape:
            self._rename_cancel()
            return True
        return super().eventFilter(obj, ev)

    def do_view_log(self):
        if NATIVE:
            d = os.path.expanduser("~/.local/share/OpenRad/logs")
            self._open_path(d if os.path.isdir(d) else os.path.join(config.HOME, "install.log"))
        else:
            self._open_path(os.path.join(config.HOME, "install.log"))

    def do_settings(self):
        """System > Settings: Radmin's General / Advanced / Notifications pages,
        plus the Linux pieces (engine, diagnostics, identity)."""
        def tog(act, text, tip=""):
            w = radui.toggle_row(text, act.isChecked(), act.setChecked, tip)
            return w
        general = [
            tog(self.actAutostart, "Start with my session (in the tray)"),
            tog(self.actVmOnStart, "Go online when the app opens" if NATIVE else "Turn the VM on when the app opens"),
            tog(self.actOnlineOnly, "Show online members only"),
            tog(self.actAuto, "Refresh the list automatically"),
        ]
        adv = [tog(self.actAutoUpd, "Automatic updates", "Once a day, one request to the GitHub releases API")]
        if NATIVE:
            adv.append(tog(self.actForceRelay, "Use relays only",
                           "No direct UDP/TCP, everything through Radmin relays. Reconnects; more ping"))
            adv += [None,
                    radui.action_button("Diagnostics", self.do_health),
                    radui.action_button("Retry failed peers", self.do_retry_peers),
                    radui.action_button("Retry the network interface", self.do_retry_interface,
                                        "Create the radminvpn0 interface again"),
                    radui.action_button("Blocked addresses…", self.do_blocked_list),
                    radui.action_button("Reset Radmin identity…", self.do_reset_identity,
                                        "New node id and 26.x address, no networks")]
        else:
            adv += [None,
                    radui.action_button("Full diagnostics (auto-repair)", self.do_health),
                    radui.action_button("Turn the VM on / off", self.toggle_power),
                    radui.action_button("Open the Radmin window (VNC)", self._open_vnc),
                    radui.action_button("Check for Radmin update…", self.do_check_update),
                    radui.action_button("Repair network (orchestrator)", self.do_orchestrate),
                    radui.action_button("Reinstall the VM agent", self.do_install_agent),
                    radui.action_button("Reset Radmin identity…", self.do_reset_identity)]
            if self._mode == "fallback":
                adv.append(radui.action_button("Sync network members", self.discover_now))
        notif = [tog(self.actNotify, "Desktop notifications",
                     "When the connection drops or an update is available")]
        dlg = radui.SettingsDialog(self, [("General", "⚙", general), ("Advanced", "☰", adv),
                                          ("Notifications", "🔔", notif)])
        for b in dlg.findChildren(QPushButton):
            if b.objectName() == "flatBtn":
                b.clicked.connect(dlg.accept)    # an action closes Settings, like a menu item
        dlg.exec()

    def _net_properties(self, name):
        net = self._live_net(name)
        if not net:
            return
        dlg = radui.RadDialog(self, f"Properties: {name}", 340)
        f, v = radui.box("Information")
        role = {2: "Administrator", 1: "Member", 0: "Waiting for approval"}.get(net.get("role"), "Unknown")
        online = sum(1 for m in net["members"] if m["ip"] in self._online)
        v.addLayout(radui.kv_grid([("Network name", name), ("Network role", role),
                                   ("Members", f"{len(net['members']) + 1}  ({online} online)"),
                                   ("Network ID", net.get("id") or "?")]))
        dlg.body.addWidget(f)
        dlg.buttons("OK", None)
        dlg.exec()

    def _peer_properties(self, ip, key):
        netname, meta, net = self._peer_ctx(ip, key)
        label = self.roster.label_of(ip)
        online = ip in self._online
        dlg = radui.RadDialog(self, f"Properties: {label}", 340)
        f, v = radui.box("Properties")
        v.addLayout(radui.kv_grid([("Name", label), ("ID", meta.get("rid") or "?"), ("IPv4", ip)]))
        dlg.body.addWidget(f)
        f, v = radui.box("Network status")
        roleBox = QComboBox(); roleBox.addItems(["Member", "Administrator"])
        role = meta.get("role")
        roleBox.setCurrentIndex(1 if role == 2 else 0)
        can = bool(net and net.get("role") == 2 and meta.get("rid") and role in (1, 2))
        roleBox.setEnabled(can)
        if not can:
            roleBox.setToolTip("Only an administrator of the network can change roles")
        v.addLayout(radui.kv_grid([("Network name", netname or "?"), ("Role", roleBox)]))
        dlg.body.addWidget(f)
        f, v = radui.box("Connection status")
        via = {"udp": "UDP", "tcp": "TCP", "tcprelay": "TCP relay"}.get(self._live_via.get(ip, "").lower(), "-")
        rtt = self._rtt.get(ip)
        v.addLayout(radui.kv_grid([("Status", "Online" if online else "Offline"),
                                   ("Channel type", via if online else "-"),
                                   ("Ping", f"{rtt:.0f} ms" if online and rtt is not None else "-")]))
        dlg.body.addWidget(f)
        f, v = radui.box("Block traffic (Linux)")
        blk = QComboBox(); blk.addItems(["Not blocked", "Both ways", "Only what it sends", "Only what I send"])
        cur = {"both": 1, "incoming": 2, "outgoing": 3}.get(self._blocked.get(ip, ""), 0)
        blk.setCurrentIndex(cur)
        blk.setToolTip("Silent and local: the member stays connected and is not told")
        v.addWidget(blk)
        dlg.body.addWidget(f)
        dlg.buttons("OK", "Cancel")
        if dlg.exec() != QDialog.Accepted:
            return
        steps = []
        if can and (roleBox.currentIndex() == 1) != (role == 2):
            nid, rid = net["id"], meta["rid"]
            steps.append((orctl.grant_admin if roleBox.currentIndex() == 1 else orctl.revoke_admin, (nid, rid)))
        if blk.currentIndex() != cur:
            if blk.currentIndex() == 0:
                steps.append((orctl.unblock, (ip,)))
            else:
                if cur:
                    steps.append((orctl.unblock, (ip,)))
                steps.append((orctl.block, (ip, ("both", "incoming", "outgoing")[blk.currentIndex() - 1])))
        if steps:
            def go():
                for fn, args in steps:
                    ok, msg = fn(*args)
                    if not ok:
                        return ok, msg
                return True, ""
            self._run_action(go, f"Saving {label}")

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
            f"Give this {'PC' if NATIVE else 'VM'} a brand new Radmin VPN identity?\n\n"
            "You get a new node and a new 26.x address and leave ALL networks. "
            "Peers will see you as a new member, and you must join your networks again.")
        if r == QMessageBox.Yes:
            self._identity_seen = ""
            if NATIVE:
                name = self.nodeName.text() if self._last_service == "Running" else os.uname().nodename
                self._run_action(lambda: orctl.reset_identity(name), "Resetting identity")
            else:
                self._run_action(actions.reset_identity, "Resetting identity")

    def do_connect(self):
        if NATIVE:
            return self.do_vm_on()
        self._run_action(actions.connect, "Connecting")

    def do_disconnect(self):
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(self, "Disconnect",
            "Disconnect from Radmin VPN?\nPeers will be unreachable until you reconnect.")
        if r == QMessageBox.Yes:
            self._user_stopped = True
            if NATIVE:
                self._run_action(lambda: (orctl.power_off(), ""), "Disconnecting")
            else:
                self._run_action(actions.disconnect, "Disconnecting")

    def do_leave(self):
        if NATIVE:
            return self._native_leave()
        if self._mode != "fallback":
            # the registry keeps networks you already left, so its list is not
            # the truth; Radmin's own window is
            self._open_gui_for("leave a network (right click it, then Leave network)")
            return
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
        if NATIVE:
            return self._native_join()
        self._open_gui_for("join a network")

    def do_create(self):
        if NATIVE:
            return self._native_create()
        self._open_gui_for("create a network")

    # ---------- native (OpenRad) network actions ----------
    def _native_ready(self) -> bool:
        if self._busy:
            return False
        if self._last_service != "Running":
            QMessageBox.information(self, "Radmin VPN", "Go online first (the power button).")
            return False
        return True

    def _native_join(self):
        if not self._native_ready():
            return
        dlg = radui.JoinNetworkDialog(self, orctl.search)
        if dlg.exec() != QDialog.Accepted:
            return
        name, pw = dlg.values()
        if name:
            self._run_action(lambda: orctl.join(name, pw or ""), f"Joining {name}")

    def _native_create(self):
        if not self._native_ready():
            return
        dlg = radui.CreateNetworkDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        name, pw = dlg.values()
        if name and self._pw_ok(pw):
            self._run_action(lambda: orctl.create(name, pw), f"Creating {name}")

    def _pw_ok(self, pw) -> bool:
        """Radmin network passwords are 6 to 256 characters (OpenRad checks it too)."""
        if 6 <= len(pw) <= 256:
            return True
        QMessageBox.information(self, "Radmin VPN", "Network passwords have 6 to 256 characters.")
        return False

    def _native_leave(self, name=None):
        if not self._native_ready():
            return
        nets = [n["name"] for n in (self._live or [])]
        if not nets:
            QMessageBox.information(self, "Leave network", "You are not in any network.")
            return
        if name is None:
            if len(nets) == 1:
                name = nets[0]
            else:
                name, ok = QInputDialog.getItem(self, "Leave network", "Which network to leave?",
                                                nets, 0, False)
                if not ok:
                    return
        r = QMessageBox.question(self, "Leave network",
            f"Leave {name}?\n\nTo come back you need its name (and password if private).")
        if r == QMessageBox.Yes:
            self._run_action(lambda: orctl.leave(name), f"Leaving {name}")

    def _live_net(self, name):
        return next((n for n in (self._live or []) if n["name"] == name), None)

    def _native_delete(self, name):
        if not self._native_ready():
            return
        net = self._live_net(name)
        if not net or net.get("role") != 2 or not net.get("id"):
            return
        typed, ok = QInputDialog.getText(self, "Delete network",
            f"Delete {name} for EVERY member?\n\nEveryone is removed and the network is gone "
            "for good, this cannot be undone.\n\nType the network name to confirm:")
        if not ok:
            return
        if typed.strip() != name:
            QMessageBox.information(self, "Delete network", "The name did not match, nothing was deleted.")
            return
        nid = net["id"]
        self._run_action(lambda: orctl.delete(nid), f"Deleting {name}")

    def _peer_ctx(self, ip, key):
        section = key.split("|", 1)[0] if key and "|" in key else ""
        netname = section[4:] if section.startswith("net:") else ""
        meta = self._live_meta.get((netname, ip)) or {}
        net = self._live_net(netname) if netname else None
        return netname, meta, net

    def _native_peer_items(self, m, ip, key):
        """Radmin's member menu, minus the Radmin Server items (remote control,
        chat, file transfer) that OpenRad does not have."""
        netname, meta, net = self._peer_ctx(ip, key)
        rid, role = meta.get("rid"), meta.get("role")
        label = self.roster.label_of(ip)
        admin = bool(net and net.get("role") == 2 and isinstance(rid, int) and rid and net.get("id"))
        m.addSeparator()
        rm = m.addAction("Remove", lambda: self._native_member("kick", net["id"], rid, label, netname))
        if role == 2:
            ga = m.addAction("Revoke Admin Rights",
                             lambda: self._native_member("revoke", net["id"], rid, label, netname))
        else:
            ga = m.addAction("Grant Admin Rights",
                             lambda: self._native_member("grant", net["id"], rid, label, netname))
        for a in (rm, ga):
            a.setEnabled(admin)
            if not admin:
                a.setToolTip("Only an administrator of the network can do this")
        m.setToolTipsVisible(True)
        m.addSeparator()
        m.addAction("Properties", lambda: self._peer_properties(ip, key))

    def _native_member(self, what, nid, rid, label, netname):
        if not self._native_ready():
            return
        if what == "kick":
            r = QMessageBox.question(self, "Remove member",
                f"Remove {label} from {netname}?\n\nThey can join again if they know the password.")
            if r != QMessageBox.Yes:
                return
            return self._run_action(lambda: orctl.kick(nid, rid), f"Removing {label}")
        if what == "grant":
            r = QMessageBox.question(self, "Make admin",
                f"Make {label} an admin of {netname}?\n\nAdmins can remove members and delete the network.")
            if r != QMessageBox.Yes:
                return
            return self._run_action(lambda: orctl.grant_admin(nid, rid), f"Making {label} admin")
        self._run_action(lambda: orctl.revoke_admin(nid, rid), f"Removing admin from {label}")

    def do_retry_peers(self):
        if self._native_ready():
            self._run_action(orctl.retry_peers, "Retrying failed peers")

    def _toggle_force_relay(self, on):
        if not self._native_ready():
            self.actForceRelay.blockSignals(True)
            self.actForceRelay.setChecked(self._force_relay)
            self.actForceRelay.blockSignals(False)
            return
        self._run_action(lambda: orctl.force_relay(bool(on)),
                         "Relays only: on" if on else "Relays only: off")

    def do_blocked_list(self):
        if not self._native_ready():
            return
        items = sorted(self._blocked.items())
        if not items:
            QMessageBox.information(self, "Blocked addresses",
                "No address is blocked.\n\nRight click a member > Block this IP.")
            return
        how = {"both": "both ways", "incoming": "what it sends", "outgoing": "what I send"}
        rows = [f"{ip}  {self.roster.label_of(ip)}  ({how.get(d, d)})" for ip, d in items]
        pick, ok = QInputDialog.getItem(self, "Blocked addresses",
            "Pick one to unblock (they never knew they were blocked):", rows, 0, False)
        if ok and pick:
            ip = pick.split()[0]
            self._run_action(lambda: orctl.unblock(ip), f"Unblocking {ip}")

    def do_retry_interface(self):
        if not self._native_ready():
            return
        self._run_action(orctl.retry_interface, "Retrying the network interface")

    def _native_warning(self, st):
        """The VPN is up but games will not see peers: say why, once per reason."""
        if st.error and st.error != getattr(self, "_native_err", ""):
            self._flash(f"● {st.error} (System > Retry the network interface)", 15)
        self._native_err = st.error

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
            self._viewers.append(subprocess.Popen(["bash", view], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True))
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
            self._keep_vm = True   # a restart, not a Quit: the VM stays on
            QApplication.quit()
        else:
            self._flash(f"● updated to {res['latest']}, restart the app to use it", 30)

    def _quit(self):
        """Quit = nothing left behind: the VM is shut down (Windows gets up to
        2 minutes, then qemu is stopped), the VNC viewers are closed, then the
        app exits. Meanwhile the tray says so."""
        if self._quitting:
            return
        if self._busy and not self._stopping and any(
                isinstance(t, ActionWorker) and t.isRunning() and t is not self._upd
                for t in self._threads):
            r = QMessageBox.question(self, "Quit",
                "An operation (update or VM action) is still running.\n"
                "Quit anyway? The VM will be turned off.")
            if r != QMessageBox.Yes:
                return
        self._quitting = True
        for t in (self.timer, self.pingtimer, self.healthtimer, self.updtimer):
            t.stop()
        self._close_viewers()
        if not vmctl.is_running() and not vmctl.starting():
            QApplication.quit()
            return
        self._stopping = True
        self._show_stopping()
        self.nodeIp.setText("the app closes when " + ("Radmin is off" if NATIVE else "the VM is off"))
        self._set_summary("● disconnecting, then quitting" if NATIVE else "● turning the VM off, then quitting")
        self._clear_list()
        self.menuBar().setEnabled(False)
        self.power.setEnabled(False)
        if self.tray.contextMenu():
            for a in self.tray.contextMenu().actions():
                a.setEnabled(a is self.trayShow)
            self.trayQuit.setText("Quitting… (disconnecting)" if NATIVE else "Quitting… (turning the VM off)")
        self._update_tray()
        if not self.isVisible() and self.tray.isVisible():
            self.tray.showMessage("Radmin VPN", ("Disconnecting." if NATIVE else "Turning the VM off.")
                                  + " Radmin VPN closes when it is done.",
                                  icons.logo_icon(64), 5000)
        w = ActionWorker(lambda: (vmctl.stop_everything(), ""))
        w.done.connect(lambda ok, log: QApplication.quit())
        self._start(w)

    def _close_viewers(self):
        import signal
        for p in self._viewers:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)   # own session: viewer + its children
                except OSError:
                    pass
        self._viewers = []
        vmctl.close_viewers()   # also viewers opened outside the app

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
        if NATIVE:
            QMessageBox.about(self, "About",
                f"Radmin VPN (Linux) {config.VERSION}\n"
                f"Engine: OpenRad {orctl.version() or '?'} (native, no VM)\n\n"
                "Talks to Radmin VPN networks with OpenRad, an open source\n"
                "Radmin VPN client (MIT, github.com/gringoestrangeiro/openrad,\n"
                "copy at github.com/gabrielmf1998/openrad).\n"
                "The power button connects and creates the radminvpn0 interface.\n\n"
                "Not affiliated with Famatech. Radmin VPN is their product.")
            return
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
        # A Quit in progress (or a restart) closes it for real: Qt 6 asks every
        # window to close before quitting and cancels the quit if one refuses.
        if self._quitting or self._keep_vm:
            e.accept()
            return
        if self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            e.ignore(); self.hide()
            if not self.roster.get_setting("tray_hint_shown", False):
                self.roster.set_setting("tray_hint_shown", True)
                self.tray.showMessage("Radmin VPN", "Still running in the tray. Quit from the tray menu.",
                                      icons.logo_icon(64), 5000)
        else:
            e.ignore()
            self._quit()

    def shutdown(self):
        # Quit already turned the VM off. Any other way out (logout, the session
        # closing the app): ask Windows to shut down without waiting, the VM
        # powers itself off a few seconds later.
        if not self._quitting and not self._keep_vm:
            self._quitting = True
            try:
                self._close_viewers()
                vmctl.cancel_start()
                vmctl.request_power_off()
            except Exception:  # noqa
                pass
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
