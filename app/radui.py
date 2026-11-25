"""
radui.py - the Radmin VPN look: colors measured on the real Radmin VPN 2.0
window (Windows, dark theme), its frameless title bar, the round power button,
the On/Off switches and the dialogs (Create Network, Join Network with the
Gaming Network tab, Settings, Properties). Pure widgets: the window wires the
actions.
"""
from __future__ import annotations
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QTimer, QThread
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QCursor, QBrush
from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QDialog, QLineEdit,
    QFrame, QGridLayout, QListWidget, QListWidgetItem, QStackedWidget, QComboBox,
    QAbstractButton, QSizePolicy, QStyledItemDelegate, QStyle,
)

import icons

# measured on the real window
BG = "#0e1621"          # window and dialog body
PANEL = "#252e3d"       # node panel, network panel, title bars
NET = "#2b5278"         # network header pill, selected row
TEAL = "#40a2b5"        # node name
BADGE = "#3992a2"       # Online / Offline badge
ON = "#29afc8"          # power button on
OFF = "#d7664a"         # power button off
RING = "#6b7886"        # power button ring
NOTICE = "#efae2c"      # orange notice bar
MENU = "#2b3d50"
MENU_SEP = "#31465c"
MENU_SEL = "#2b62ac"
INPUT = "#2c5078"
BTN = "#376da5"
BTN_OFF = "#222a35"
TAB_OFF = "#1e2531"
GREEN = "#00ab00"
LOGO = "#10cbee"
LOGO_VPN = "#5a7588"

QSS = f"""
* {{ color: #ffffff; font-family: 'Segoe UI','Noto Sans',sans-serif; font-size: 12px; }}
#root, QDialog {{ background: {BG}; }}
QToolTip {{ background: {MENU}; color: #ffffff; border: 1px solid {MENU_SEP}; padding: 3px; }}
#titleBar, #dlgTitle {{ background: {PANEL}; }}
#dlgTitleText {{ font-size: 14px; }}
#titleBtn {{ background: transparent; border: none; color: #c9d2da; font-size: 15px; padding: 0 8px; }}
#titleBtn:hover {{ background: #33404f; color: #ffffff; }}
#titleClose:hover {{ background: #c0392b; color: #ffffff; }}
#titleClose {{ background: transparent; border: none; color: #c9d2da; font-size: 15px; padding: 0 8px; }}
#menubar {{ background: {BG}; color: #ffffff; }}
QMenuBar::item {{ padding: 3px 4px; background: transparent; border-radius: 3px; }}
QMenuBar::item:selected {{ background: {MENU}; }}
QMenu {{ background: {MENU}; border: 1px solid {MENU_SEP}; padding: 3px 0; }}
QMenu::item {{ padding: 4px 26px 4px 26px; }}
QMenu::item:selected {{ background: {MENU_SEL}; }}
QMenu::item:disabled {{ color: #7d8b99; }}
QMenu::separator {{ height: 1px; background: {MENU_SEP}; margin: 3px 8px; }}
#topPanel, #netPanel {{ background: {PANEL}; border-radius: 6px; }}
#nodeName {{ color: {TEAL}; font-size: 16px; font-weight: 700; }}
#nodeNameEdit {{ background: {INPUT}; border: 1px solid #4a7fb0; color: #ffffff;
                 font-size: 14px; padding: 1px 4px; border-radius: 2px; }}
#nodeIp {{ color: #ffffff; font-size: 13px; font-weight: 700; }}
#badge {{ background: {BADGE}; color: #ffffff; border-radius: 5px; padding: 2px 7px;
          font-size: 13px; font-weight: 700; }}
#notice {{ background: {NOTICE}; color: #ffffff; border-radius: 9px; padding: 2px 10px;
           font-size: 13px; font-weight: 700; }}
#notice[kind="info"] {{ background: {NET}; }}
#hint {{ color: #7d8b99; font-size: 12px; }}
QScrollArea, #list {{ background: {BG}; border: none; }}
#peerRow {{ background: transparent; border-radius: 10px; }}
#peerRow:hover, #peerRow[sel="true"] {{ background: {NET}; }}
.peerName {{ font-size: 12px; color: #ffffff; }}
.peerName[off="true"] {{ color: #c3cbd3; }}
.peerIp {{ font-size: 12px; color: #ffffff; }}
#netHeader {{ background: {NET}; border-radius: 11px; }}
.netHeaderName {{ color: #ffffff; font-size: 13px; font-weight: 700; }}
.netHeaderCount {{ color: #a9c1d8; font-size: 11px; }}
QScrollBar:vertical {{ background: {BG}; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3a4a5c; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3a4a5c; border-radius: 4px; min-width: 24px; }}
QLineEdit {{ background: {INPUT}; border: 1px solid {INPUT}; border-radius: 2px; padding: 3px 5px;
             color: #ffffff; selection-background-color: {MENU_SEL}; }}
QLineEdit:focus {{ border: 1px solid #4f8fd0; }}
QLineEdit:disabled {{ background: #1f2b3a; border-color: #1f2b3a; color: #7d8b99; }}
QPushButton#dlgBtn {{ background: {BTN}; border: 1px solid {BTN}; border-radius: 3px; padding: 5px 14px;
                      min-width: 66px; font-size: 13px; }}
QPushButton#dlgBtn:hover {{ background: #4280c0; }}
QPushButton#dlgBtn:disabled {{ background: {BTN_OFF}; border-color: {BTN_OFF}; color: #8aa0b0; }}
QPushButton#flatBtn {{ background: {PANEL}; border: 1px solid #34465a; border-radius: 3px;
                       padding: 5px 12px; text-align: left; }}
QPushButton#flatBtn:hover {{ background: {NET}; }}
#box {{ background: {PANEL}; border-radius: 8px; }}
#boxTitle {{ color: #4f8fd0; font-size: 12px; }}
#muted {{ color: #9aa7b4; }}
QComboBox {{ background: {INPUT}; border: none; border-radius: 2px; padding: 3px 6px; color: #ffffff; }}
QComboBox:disabled {{ background: #1f2b3a; color: #9aa7b4; }}
QComboBox QAbstractItemView {{ background: {MENU}; selection-background-color: {MENU_SEL}; border: 1px solid {MENU_SEP}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
#tabOn {{ background: {PANEL}; border: none; border-top-left-radius: 4px; border-top-right-radius: 4px;
          padding: 4px 8px; font-weight: 700; }}
#tabOff {{ background: {TAB_OFF}; border: none; border-top-left-radius: 4px; border-top-right-radius: 4px;
           padding: 4px 8px; font-weight: 700; color: #d2d2d2; }}
#tabBody {{ background: {PANEL}; border-radius: 6px; border-top-left-radius: 0; }}
QListWidget#gaming {{ background: {BG}; border: none; outline: none; }}
QListWidget#gaming::item {{ padding: 1px 2px; }}
QListWidget#gaming::item:selected {{ background: {NET}; border-radius: 3px; }}
QListWidget#side {{ background: {PANEL}; border: none; outline: none; }}
QListWidget#side::item {{ padding: 8px 10px; }}
QListWidget#side::item:selected {{ background: #3a73ae; }}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid #4f8fd0; border-radius: 2px; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {BTN}; }}
"""


# ------------------------------------------------------------------ title bars
class TitleBar(QWidget):
    """Radmin's frameless title: logo + RADMIN VPN centered, minimize and close.
    Drag it to move the window (Wayland too: startSystemMove)."""
    def __init__(self, window, on_close, on_min=None):
        super().__init__()
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(34)
        self._win = window
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addSpacing(64)
        lay.addStretch(1)
        logo = QLabel(); logo.setPixmap(icons.logo_pixmap(24)); lay.addWidget(logo)
        lay.addSpacing(5)
        t = QLabel(f"<span style='color:{LOGO}'>R<span style='font-size:13px'>ADMIN</span></span>"
                   f"&nbsp;<span style='color:{LOGO_VPN}'>VPN</span>")
        t.setStyleSheet("font-size: 17px; font-weight: 700; letter-spacing: 1px;")
        lay.addWidget(t)
        lay.addStretch(1)
        if on_min:
            b = QPushButton("—"); b.setObjectName("titleBtn"); b.setFixedSize(32, 34)
            b.setToolTip("Minimize"); b.clicked.connect(on_min); lay.addWidget(b)
        else:
            lay.addSpacing(32)
        c = QPushButton("✕"); c.setObjectName("titleClose"); c.setFixedSize(32, 34)
        c.setToolTip("Close to the tray"); c.clicked.connect(on_close); lay.addWidget(c)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._win.windowHandle():
            self._win.windowHandle().startSystemMove()


class DialogTitle(QWidget):
    def __init__(self, dialog, text):
        super().__init__()
        self.setObjectName("dlgTitle")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(32)
        self._dlg = dialog
        lay = QHBoxLayout(self); lay.setContentsMargins(9, 0, 0, 0)
        self.text = QLabel(text); self.text.setObjectName("dlgTitleText"); lay.addWidget(self.text, 1)
        c = QPushButton("✕"); c.setObjectName("titleClose"); c.setFixedSize(32, 32)
        c.clicked.connect(dialog.reject); lay.addWidget(c)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._dlg.windowHandle():
            self._dlg.windowHandle().startSystemMove()


class RadDialog(QDialog):
    """Frameless dialog with Radmin's title bar, body and OK/Cancel row."""
    def __init__(self, parent, title, width=360):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setStyleSheet(QSS)
        self.setMinimumWidth(width)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(DialogTitle(self, title))
        border = QFrame(); border.setStyleSheet(f"QFrame#dlgFrame {{ border: 1px solid {PANEL}; border-top: none; }}")
        border.setObjectName("dlgFrame")
        outer.addWidget(border, 1)
        self.body = QVBoxLayout(border); self.body.setContentsMargins(10, 10, 10, 10); self.body.setSpacing(8)

    def buttons(self, ok_text="OK", cancel_text="Cancel"):
        row = QHBoxLayout(); row.addStretch(1)
        self.okBtn = None
        if ok_text:
            self.okBtn = QPushButton(ok_text); self.okBtn.setObjectName("dlgBtn")
            self.okBtn.clicked.connect(self.accept); self.okBtn.setDefault(True); row.addWidget(self.okBtn)
        if cancel_text:
            c = QPushButton(cancel_text); c.setObjectName("dlgBtn"); c.clicked.connect(self.reject)
            row.addWidget(c)
        self.body.addLayout(row)
        return self.okBtn


def box(title):
    """Radmin's titled group: a rounded panel with a small blue caption."""
    f = QFrame(); f.setObjectName("box")
    v = QVBoxLayout(f); v.setContentsMargins(10, 4, 10, 8); v.setSpacing(4)
    t = QLabel(title); t.setObjectName("boxTitle"); v.addWidget(t)
    return f, v


def kv_grid(rows):
    g = QGridLayout(); g.setHorizontalSpacing(8); g.setVerticalSpacing(4)
    for i, (k, v) in enumerate(rows):
        g.addWidget(QLabel(f"{k}:"), i, 0)
        if isinstance(v, QWidget):
            g.addWidget(v, i, 1)
        else:
            lab = QLabel(str(v)); lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            g.addWidget(lab, i, 1)
    g.setColumnStretch(1, 1)
    return g


# ------------------------------------------------------------------ main window parts
class PowerButton(QAbstractButton):
    """The big round ON / OFF button. state: on, off or busy."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(66, 66)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._state = "off"

    def set_state(self, state):
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        p.setPen(Qt.NoPen); p.setBrush(QColor(RING)); p.drawEllipse(r)
        p.setBrush(QColor("#c9dde6")); p.drawEllipse(r.adjusted(3, 3, -3, -3))
        fill = {"on": ON, "off": OFF}.get(self._state, "#6aa9b8")
        c = QColor(fill)
        if self.underMouse():
            c = c.lighter(110)
        p.setBrush(c); p.drawEllipse(r.adjusted(5, 5, -5, -5))
        # power glyph
        pen = QPen(QColor("#ffffff")); pen.setWidthF(2.4); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen); p.setBrush(Qt.NoBrush)
        cx, cy, rad = self.width() / 2, self.height() / 2 - 6, 8.0
        p.drawArc(QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad), 125 * 16, 290 * 16)
        p.drawLine(QPointF(cx, cy - rad - 2), QPointF(cx, cy + 1))
        f = QFont(self.font()); f.setPixelSize(12); f.setBold(True); p.setFont(f)
        label = {"on": "ON", "off": "OFF"}.get(self._state, "…")
        p.drawText(QRectF(0, cy + rad + 1, self.width(), 16), Qt.AlignHCenter | Qt.AlignTop, label)
        p.end()

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


class Toggle(QAbstractButton):
    """Radmin's On/Off switch with the label next to it."""
    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True); self.setChecked(checked)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFixedSize(64, 22)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QRectF(1, 6, 30, 11)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1f7f92" if on else "#3a4656")); p.drawRoundedRect(track, 5.5, 5.5)
        knob = QRectF(15 if on else 0, 2, 18, 18)
        p.setBrush(QColor(ON if on else "#f2f4f6")); p.drawEllipse(knob)
        p.setPen(QColor("#ffffff"))
        f = QFont(self.font()); f.setPixelSize(12); p.setFont(f)
        p.drawText(QRectF(40, 0, 30, 22), Qt.AlignVCenter | Qt.AlignLeft, "On" if on else "Off")
        if not self.isEnabled():
            p.fillRect(self.rect(), QColor(14, 22, 33, 140))
        p.end()


# ------------------------------------------------------------------ dialogs
class CreateNetworkDialog(RadDialog):
    def __init__(self, parent):
        super().__init__(parent, "Create Network", 360)
        g = QGridLayout(); g.setVerticalSpacing(8)
        self.name = QLineEdit(); self.pw = QLineEdit(); self.pw2 = QLineEdit()
        for w in (self.pw, self.pw2):
            w.setEchoMode(QLineEdit.Password)
        for i, (lab, w) in enumerate((("Network name:", self.name), ("Password:", self.pw),
                                      ("Confirm password:", self.pw2))):
            g.addWidget(QLabel(lab), i, 0); g.addWidget(w, i, 1)
            w.textChanged.connect(self._check)
        self.body.addLayout(g)
        self.err = QLabel(""); self.err.setObjectName("muted"); self.body.addWidget(self.err)
        self.buttons("Create", "Cancel")
        self._check()

    def _check(self):
        name, pw, pw2 = self.name.text().strip(), self.pw.text(), self.pw2.text()
        msg = ""
        if pw and not 6 <= len(pw) <= 256:
            msg = "The password needs 6 to 256 characters."
        elif pw2 and pw != pw2:
            msg = "The passwords do not match."
        self.err.setText(msg)
        self.okBtn.setEnabled(bool(name) and 6 <= len(pw) <= 256 and pw == pw2)

    def values(self):
        return self.name.text().strip(), self.pw.text()


class _SearchWorker(QThread):
    done = Signal(object)

    def __init__(self, fn, query):
        super().__init__()
        self.fn, self.query = fn, query

    def run(self):
        try:
            self.done.emit(self.fn(self.query))
        except Exception:  # noqa
            self.done.emit([])


class _CountDelegate(QStyledItemDelegate):
    """Gaming list row: the blue member count pill, then the network name."""
    def paint(self, p, opt, idx):
        p.save(); p.setRenderHint(QPainter.Antialiasing)
        if opt.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen); p.setBrush(QColor(NET)); p.drawRoundedRect(QRectF(opt.rect), 3, 3)
        count = str(idx.data(Qt.UserRole + 1) or "")
        f = QFont(opt.font); f.setPixelSize(9); f.setBold(True); p.setFont(f)
        w = max(22, p.fontMetrics().horizontalAdvance(count) + 10)
        pill = QRectF(opt.rect.left() + 4, opt.rect.top() + 3, w, opt.rect.height() - 6)
        p.setPen(Qt.NoPen); p.setBrush(QColor(BTN)); p.drawRoundedRect(pill, 6, 6)
        p.setPen(QColor("#ffffff")); p.drawText(pill, Qt.AlignCenter, count)
        f.setPixelSize(12); f.setBold(False); p.setFont(f)
        p.drawText(QRectF(pill.right() + 6, opt.rect.top(), opt.rect.width() - w - 14, opt.rect.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, str(idx.data(Qt.DisplayRole) or ""))
        p.restore()

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx); s.setHeight(18); return s


class JoinNetworkDialog(RadDialog):
    """Private Network (name + password) or Gaming Network (public list)."""
    def __init__(self, parent, search_fn=None):
        super().__init__(parent, "Join Network", 370)
        self._search_fn = search_fn
        tabs = QHBoxLayout(); tabs.setSpacing(2); tabs.setContentsMargins(0, 0, 0, 0)
        self.tPriv = QPushButton("Private Network"); self.tGame = QPushButton("Gaming Network")
        for b in (self.tPriv, self.tGame):
            b.setCursor(QCursor(Qt.PointingHandCursor)); tabs.addWidget(b)
        tabs.addStretch(1)
        self.body.addLayout(tabs)
        self.body.setSpacing(0)
        self.stack = QStackedWidget(); self.stack.setObjectName("tabBody")
        self.stack.setAttribute(Qt.WA_StyledBackground, True)
        self.body.addWidget(self.stack)
        # private
        pw = QWidget(); g = QGridLayout(pw); g.setContentsMargins(8, 10, 8, 10); g.setVerticalSpacing(8)
        self.name = QLineEdit(); self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.Password)
        g.addWidget(QLabel("Network name:"), 0, 0); g.addWidget(self.name, 0, 1)
        g.addWidget(QLabel("Password:"), 1, 0); g.addWidget(self.pw, 1, 1)
        self.stack.addWidget(pw)
        # gaming
        gw = QWidget(); gv = QVBoxLayout(gw); gv.setContentsMargins(8, 8, 8, 8); gv.setSpacing(6)
        sr = QHBoxLayout(); sr.addWidget(QLabel("Which game would you like to play?"))
        self.search = QLineEdit(); self.search.setPlaceholderText("Search"); sr.addWidget(self.search, 1)
        gv.addLayout(sr)
        self.list = QListWidget(); self.list.setObjectName("gaming"); self.list.setMinimumHeight(230)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self._delegate = _CountDelegate(self.list)   # keep a reference: a collected delegate segfaults
        self.list.setItemDelegate(self._delegate)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        gv.addWidget(self.list)
        self.state = QLabel(""); self.state.setObjectName("muted"); gv.addWidget(self.state)
        self.stack.addWidget(gw)
        self.body.addSpacing(10)
        self.err = QLabel(""); self.err.setObjectName("muted"); self.body.addWidget(self.err)
        self.body.addSpacing(4)
        self.buttons("Join", "Cancel")
        self.tPriv.clicked.connect(lambda: self._tab(0))
        self.tGame.clicked.connect(lambda: self._tab(1))
        if not search_fn:
            self.tGame.hide()
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(450)
        self._timer.timeout.connect(self._do_search)
        self.search.textChanged.connect(lambda *_: self._timer.start())
        for w in (self.name, self.pw):
            w.textChanged.connect(self._check)
        self.list.currentItemChanged.connect(lambda *_: self._check())
        self._worker = None
        self._loaded = False
        self._tab(0)

    def _tab(self, i):
        self.stack.setCurrentIndex(i)
        # only the visible page counts for the size (Private is short, Gaming tall)
        for n in range(self.stack.count()):
            pol = QSizePolicy.Preferred if n == i else QSizePolicy.Ignored
            self.stack.widget(n).setSizePolicy(pol, pol)
        self.stack.adjustSize()
        self.tPriv.setObjectName("tabOn" if i == 0 else "tabOff")
        self.tGame.setObjectName("tabOn" if i == 1 else "tabOff")
        for b in (self.tPriv, self.tGame):
            b.style().unpolish(b); b.style().polish(b)
        if i == 1 and not self._loaded:
            self._loaded = True
            self._do_search()
        width = 370 if i == 0 else max(self.width(), 490)
        def fit():
            self.layout().invalidate(); self.layout().activate()
            self.resize(width, self.layout().sizeHint().height())
        QTimer.singleShot(0, fit)
        self._check()

    def _do_search(self):
        if not self._search_fn or (self._worker and self._worker.isRunning()):
            if self._worker and self._worker.isRunning():
                self._timer.start()
            return
        self.state.setText("Searching…")
        self._worker = _SearchWorker(self._search_fn, self.search.text().strip())
        self._worker.done.connect(self._fill)
        self._worker.start()

    def _fill(self, nets):
        self.list.clear()
        for n in sorted(nets or [], key=lambda n: -int(n.get("reported_count") or 0)):
            it = QListWidgetItem(str(n.get("name") or ""))
            it.setData(Qt.UserRole + 1, int(n.get("reported_count") or 0))
            self.list.addItem(it)
        self.state.setText("" if nets else "No network found (is Radmin on?)")
        self._check()

    def _check(self):
        msg = ""
        if self.stack.currentIndex() == 0:
            pw = self.pw.text()
            if pw and not 6 <= len(pw) <= 256:
                msg = "The password needs 6 to 256 characters."
            ok = bool(self.name.text().strip()) and not msg
        else:
            ok = self.list.currentItem() is not None
        self.err.setText(msg)
        self.err.setVisible(bool(msg))
        self.okBtn.setEnabled(ok)

    def values(self):
        """(name, password); password is "" for a gaming (public) network."""
        if self.stack.currentIndex() == 1 and self.list.currentItem():
            return self.list.currentItem().text(), ""
        return self.name.text().strip(), self.pw.text()

    def done(self, r):
        if self._worker and self._worker.isRunning():
            self._worker.wait(3000)
        super().done(r)


class SettingsDialog(RadDialog):
    """General / Advanced / Notifications, Radmin's left sidebar layout.
    pages: [(title, glyph, [widgets...])]"""
    def __init__(self, parent, pages):
        super().__init__(parent, "Settings", 430)
        row = QHBoxLayout(); row.setSpacing(8)
        self.side = QListWidget(); self.side.setObjectName("side"); self.side.setFixedWidth(120)
        self.stack = QStackedWidget()
        for title, glyph, widgets in pages:
            self.side.addItem(QListWidgetItem(f"{glyph}  {title}"))
            pg = QWidget(); v = QVBoxLayout(pg); v.setContentsMargins(2, 0, 2, 0); v.setSpacing(6)
            for w in widgets:
                if isinstance(w, str):
                    lab = QLabel(w); v.addWidget(lab)
                elif w is None:
                    v.addSpacing(8)
                else:
                    v.addWidget(w)
            v.addStretch(1)
            self.stack.addWidget(pg)
        self.side.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.side.setCurrentRow(0)
        row.addWidget(self.side); row.addWidget(self.stack, 1)
        self.body.addLayout(row)
        self.setMinimumHeight(340)


def toggle_row(text, checked, on_change, tip=""):
    """A caption over an On/Off switch, like Radmin's settings pages."""
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 2); v.setSpacing(2)
    v.addWidget(QLabel(text))
    t = Toggle(checked); t.toggled.connect(on_change); v.addWidget(t)
    if tip:
        w.setToolTip(tip)
    w.toggle = t
    return w


def action_button(text, fn, tip=""):
    b = QPushButton(text); b.setObjectName("flatBtn"); b.setCursor(QCursor(Qt.PointingHandCursor))
    b.clicked.connect(fn)
    if tip:
        b.setToolTip(tip)
    return b
