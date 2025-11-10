# ui.py
from qgis.PyQt.QtCore import Qt, pyqtSignal, QEvent
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QSizePolicy, QLineEdit, QCheckBox, QShortcut, QApplication
)
from qgis.utils import iface as _iface


def _ensure_singleton_dock(iface, object_name: str):
    from qgis.PyQt.QtWidgets import QDockWidget
    for w in iface.mainWindow().findChildren(QDockWidget):
        if w.objectName() == object_name:
            w.close()
            w.deleteLater()

class PhotoViewerDock(QDockWidget):
    prevRequested = pyqtSignal()
    nextRequested = pyqtSignal()
    configRequested = pyqtSignal()
    gmapsRequested = pyqtSignal()
    addModeToggled = pyqtSignal(bool)
    delModeToggled = pyqtSignal(bool)
    autoZoomToggled = pyqtSignal(bool)
    importClicksRequested = pyqtSignal()
    exportClicksRequested = pyqtSignal()
    jumpRequested = pyqtSignal(str)

    imageDoubleClicked = pyqtSignal(str)

    OBJECT_NAME = "PhotoViewerDockPlus"

    def __init__(self, iface, auto_zoom_default: bool = True, parent=None):
        super().__init__("PhotoViewer", parent or iface.mainWindow())
        self.setObjectName(self.OBJECT_NAME)

        # ---- PyQt5/6 互換：Dock/Key/Eventの列挙を吸収 ----
        self._DockEnum = getattr(Qt, "DockWidgetArea", Qt)
        self._RIGHT_DOCK = getattr(self._DockEnum, "RightDockWidgetArea",
                                   getattr(Qt, "RightDockWidgetArea", 2))
        self._KeyEnum = getattr(Qt, "Key", Qt)
        self._KEY_LEFT = getattr(self._KeyEnum, "Key_Left", getattr(Qt, "Key_Left", 0x01000012))
        self._KEY_RIGHT = getattr(self._KeyEnum, "Key_Right", getattr(Qt, "Key_Right", 0x01000014))
        self._EventType = getattr(QEvent, "Type", QEvent)

        root = QWidget()
        self.setWidget(root)
        layout_root = QVBoxLayout(root)
        layout_root.setContentsMargins(6, 6, 6, 6)
        layout_root.setSpacing(4)

        self.img_label_front = QLabel("⚙ Select CSV and image folder to start")
        self.img_label_back = QLabel("⚙ Select CSV and image folder to start")
        for lab in (self.img_label_front, self.img_label_back):
            lab.setAlignment(Qt.AlignCenter)
            lab.setMinimumSize(100, 280)
            lab.setScaledContents(False)
            lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            lab.setStyleSheet("border: 1px solid #999; background-color:#fdfdfd;")

        def _mk_dblclick(side: str):
            def _handler(ev):
                self.imageDoubleClicked.emit(side)
            return _handler

        self.img_label_front.mouseDoubleClickEvent = _mk_dblclick("front")
        self.img_label_back.mouseDoubleClickEvent = _mk_dblclick("back")

        self.inline_name_front = QLabel()
        self.inline_name_back = QLabel()

        def _titled_box(title: str, img_label: QLabel, color: str, inline_name_label: QLabel):
            box = QVBoxLayout()
            head = QHBoxLayout()

            t = QLabel(title)
            t.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            t.setStyleSheet(f"font-weight:bold; color:{color}; font-size:11pt;")
            head.addWidget(t)

            inline_name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            inline_name_label.setText("—")
            inline_name_label.setToolTip("")
            inline_name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            inline_name_label.setMinimumWidth(80)
            inline_name_label.setWordWrap(False)
            inline_name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

            head.addSpacing(8)
            head.addWidget(inline_name_label, 1)

            box.addLayout(head)
            box.addWidget(img_label, 1)
            return box

        img_area = QVBoxLayout()
        img_area.addLayout(_titled_box("Front（前方）", self.img_label_front, "#0078d7", self.inline_name_front), 1)
        img_area.addLayout(_titled_box("Back（後方）", self.img_label_back, "#d74100", self.inline_name_back), 1)

        # --- 操作列（ボタン等）
        btns_box = QVBoxLayout()
        btns_box.setContentsMargins(0, 0, 0, 0)
        btns_box.setSpacing(4)

        # ボタン作成
        self.prev_btn = QPushButton("◀ Previous")
        self.next_btn = QPushButton("Next ▶")
        self.cfg_btn = QPushButton("⚙ Select Master Data")
        self.gmaps_btn = QPushButton("🌐 Street View")
        self.add_btn = QPushButton("● Add Mode"); self.add_btn.setCheckable(True)
        self.add_btn.setToolTip("When ON, Clicking the map will add points to PhotoClicks")
        self.del_btn = QPushButton("✖ Delete Mode"); self.del_btn.setCheckable(True)
        self.del_btn.setToolTip("When ON, Clicking the map will delete points from PhotoClicks")
        self.zoom_chk = QCheckBox("Auto Zoom"); self.zoom_chk.setChecked(bool(auto_zoom_default))
        self.import_clicks_btn = QPushButton("⏯ Resume ")
        self.import_clicks_btn.setToolTip("Load previous click data and resume the session")
        self.export_clicks_btn = QPushButton("💾　Save ")
        self.export_clicks_btn.setToolTip("Save current clicks to a file")

        for b in (self.prev_btn, self.next_btn, self.cfg_btn, self.gmaps_btn,
                self.add_btn, self.del_btn, self.import_clicks_btn, self.export_clicks_btn):
            b.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            b.setMinimumWidth(60)

        # 1行目：移動系・表示系・モード切替・チェック
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        for w in (self.prev_btn, self.next_btn, self.gmaps_btn, self.add_btn, self.del_btn, self.zoom_chk):
            row1.addWidget(w)
        row1.addStretch(1)

        # 2行目：データ操作系（指定の3つ）
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        for w in (self.cfg_btn, self.import_clicks_btn, self.export_clicks_btn):
            row2.addWidget(w)
        row2.addStretch(1)

        btns_box.addLayout(row1)
        btns_box.addLayout(row2)

        # --- クイック検索 ---
        quick_area = QHBoxLayout()
        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("Jump by KP or image name.. Press Enter to jump")
        self.q_btn = QPushButton("Jump")
        quick_area.addWidget(self.q_edit, 1)
        quick_area.addWidget(self.q_btn)

        # --- レイアウト合成---
        layout_root.addLayout(img_area, 1)
        layout_root.addLayout(btns_box, 0)
        layout_root.addLayout(quick_area, 0)

        # ★★ テーマに応じてボタン文字色を自動調整 ★★
        self._apply_dynamic_button_text_color()

        # --- ショートカット（左右キー）
        QShortcut(QKeySequence(self._KEY_LEFT), self, activated=self.prevRequested.emit)
        QShortcut(QKeySequence(self._KEY_RIGHT), self, activated=self.nextRequested.emit)

        # --- シグナル配線（UI → 外部へ）
        self.prev_btn.clicked.connect(self.prevRequested.emit)
        self.next_btn.clicked.connect(self.nextRequested.emit)
        self.cfg_btn.clicked.connect(self.configRequested.emit)
        self.gmaps_btn.clicked.connect(self.gmapsRequested.emit)
        self.add_btn.toggled.connect(self.addModeToggled.emit)
        self.del_btn.toggled.connect(self.delModeToggled.emit)
        self.zoom_chk.toggled.connect(self.autoZoomToggled.emit)
        self.import_clicks_btn.clicked.connect(self.importClicksRequested.emit)
        self.export_clicks_btn.clicked.connect(self.exportClicksRequested.emit)
        self.q_btn.clicked.connect(lambda: self.jumpRequested.emit(self.q_edit.text().strip()))
        self.q_edit.returnPressed.connect(lambda: self.jumpRequested.emit(self.q_edit.text().strip()))

        # Dock を初期表示（右側）
        iface.addDockWidget(self._RIGHT_DOCK, self)
        self.show()

    # ------ テーマ変化に追従するためのヘルパー ------
    def _current_background_lightness(self) -> int:
        """現在のウィンドウ背景の明度(0-255)を返す"""
        pal = self.palette() or QApplication.instance().palette()
        return pal.window().color().lightness()

    def _pick_button_text_color(self) -> str:
        """背景が明るければ黒、暗ければ白を返す"""
        return "#000" if self._current_background_lightness() > 128 else "#fff"

    # """文字色を現在テーマに合わせて適用"""
    def _apply_dynamic_button_text_color(self):
        root = self.widget()
        text_color = self._pick_button_text_color()
        root.setStyleSheet(f"""
        QPushButton {{ color: {text_color}; }}
        QPushButton:checked {{ color: {text_color}; }}
        QPushButton:hover {{ color: {text_color}; }}
        QPushButton:disabled {{ color: #888; }}
        """)
        self.inline_name_front.setStyleSheet(
        f"color:{text_color}; font-family: Menlo, 'Courier New', monospace; font-size:10px;")
        self.inline_name_back.setStyleSheet(
        f"color:{text_color}; font-family: Menlo, 'Courier New', monospace; font-size:10px;")

    def changeEvent(self, ev):
        if ev.type() in (
            getattr(self._EventType, "PaletteChange", QEvent.PaletteChange),
            getattr(self._EventType, "ApplicationPaletteChange", QEvent.ApplicationPaletteChange),
            getattr(self._EventType, "StyleChange", QEvent.StyleChange),
        ):
            self._apply_dynamic_button_text_color()
        super().changeEvent(ev)

    # --- 外部 API（viewer.py から使うユーティリティ） -----------------
    def set_inline_names(self, front_text: str = "—", front_tooltip: str = "",
                         back_text: str = "—", back_tooltip: str = ""):
        self.inline_name_front.setText(front_text or "—")
        self.inline_name_front.setToolTip(front_tooltip or "")
        self.inline_name_back.setText(back_text or "—")
        self.inline_name_back.setToolTip(back_tooltip or "")

    @property
    def frontLabel(self) -> QLabel:
        return self.img_label_front

    @property
    def backLabel(self) -> QLabel:
        return self.img_label_back

    def setAddButtonChecked(self, checked: bool):
        self.add_btn.setChecked(bool(checked))

    def setDelButtonChecked(self, checked: bool):
        self.del_btn.setChecked(bool(checked))

    def setAutoZoomChecked(self, checked: bool):
        self.zoom_chk.setChecked(bool(checked))

    def set_message(self, side: str, text: str):
        lab = self.img_label_front if side == "front" else self.img_label_back
        lab.clear()
        lab.setText(text or "")

    def set_pixmap(self, side: str, pm):
        lab = self.img_label_front if side == "front" else self.img_label_back
        if pm is None or pm.isNull():
            lab.clear()
            return

        lab.setPixmap(pm.scaledToWidth(max(1, lab.width()), Qt.SmoothTransformation))
        
        # リサイズで再フィット（1回だけ差し替える）
        if not hasattr(lab, "_pv_orig_resizeEvent"):
            lab._pv_orig_resizeEvent = lab.resizeEvent
        def _resize(ev):
            cur = lab.pixmap()
            if cur and not cur.isNull():
                lab.setPixmap(cur.scaledToWidth(max(1, lab.width()), Qt.SmoothTransformation))
            if getattr(lab, "_pv_orig_resizeEvent", None):
                lab._pv_orig_resizeEvent(ev)
        lab.resizeEvent = _resize

def create_dock(auto_zoom_default: bool = True, iface=_iface) -> PhotoViewerDock:
    _ensure_singleton_dock(iface, PhotoViewerDock.OBJECT_NAME)
    return PhotoViewerDock(iface=iface, auto_zoom_default=auto_zoom_default, parent=iface.mainWindow())
