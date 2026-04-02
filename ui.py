from qgis.PyQt.QtCore import Qt, pyqtSignal, QEvent
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QSizePolicy, QLineEdit, QCheckBox, QShortcut, QApplication
)
from qgis.utils import iface as _iface


def _qt_enum(container, scoped_name: str, legacy_name: str = None, default=None):
    """
    Qt5/Qt6 両対応で enum 値を取得する。
    例:
        _qt_enum(Qt, "AlignmentFlag.AlignCenter", "AlignCenter")
        _qt_enum(QSizePolicy, "Policy.Expanding", "Expanding")
        _qt_enum(QEvent, "Type.PaletteChange", "PaletteChange")
    """
    obj = container
    try:
        for part in scoped_name.split("."):
            obj = getattr(obj, part)
        return obj
    except AttributeError:
        pass

    if legacy_name is not None:
        try:
            return getattr(container, legacy_name)
        except AttributeError:
            pass

    if default is not None:
        return default

    raise AttributeError(
        f"Could not resolve Qt enum: {container}.{scoped_name}"
        + (f" or legacy {legacy_name}" if legacy_name else "")
    )


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
    editModeToggled = pyqtSignal(bool)
    autoZoomToggled = pyqtSignal(bool)
    importClicksRequested = pyqtSignal()
    exportClicksRequested = pyqtSignal()
    jumpRequested = pyqtSignal(str)

    imageDoubleClicked = pyqtSignal(str)

    OBJECT_NAME = "PhotoViewerDockPlus"

    def __init__(self, iface, auto_zoom_default: bool = True, parent=None):
        super().__init__("PhotoViewer", parent or iface.mainWindow())
        self.setObjectName(self.OBJECT_NAME)

        # ---- Qt5/Qt6 互換 enum 吸収 ----
        self._RIGHT_DOCK = _qt_enum(
            Qt, "DockWidgetArea.RightDockWidgetArea", "RightDockWidgetArea", 2
        )

        self._KEY_LEFT = _qt_enum(
            Qt, "Key.Key_Left", "Key_Left", 0x01000012
        )
        self._KEY_RIGHT = _qt_enum(
            Qt, "Key.Key_Right", "Key_Right", 0x01000014
        )

        self._ALIGN_CENTER = _qt_enum(
            Qt, "AlignmentFlag.AlignCenter", "AlignCenter"
        )
        self._ALIGN_LEFT = _qt_enum(
            Qt, "AlignmentFlag.AlignLeft", "AlignLeft"
        )
        self._ALIGN_VCENTER = _qt_enum(
            Qt, "AlignmentFlag.AlignVCenter", "AlignVCenter"
        )

        self._TEXT_SELECTABLE_BY_MOUSE = _qt_enum(
            Qt, "TextInteractionFlag.TextSelectableByMouse", "TextSelectableByMouse"
        )

        self._SMOOTH_TRANSFORM = _qt_enum(
            Qt, "TransformationMode.SmoothTransformation", "SmoothTransformation"
        )

        self._SIZEPOLICY_EXPANDING = _qt_enum(
            QSizePolicy, "Policy.Expanding", "Expanding"
        )
        self._SIZEPOLICY_FIXED = _qt_enum(
            QSizePolicy, "Policy.Fixed", "Fixed"
        )
        self._SIZEPOLICY_PREFERRED = _qt_enum(
            QSizePolicy, "Policy.Preferred", "Preferred"
        )
        self._SIZEPOLICY_IGNORED = _qt_enum(
            QSizePolicy, "Policy.Ignored", "Ignored"
        )

        self._EVENT_PALETTE_CHANGE = _qt_enum(
            QEvent, "Type.PaletteChange", "PaletteChange", None
        )
        self._EVENT_APP_PALETTE_CHANGE = _qt_enum(
            QEvent, "Type.ApplicationPaletteChange", "ApplicationPaletteChange", None
        )
        self._EVENT_STYLE_CHANGE = _qt_enum(
            QEvent, "Type.StyleChange", "StyleChange", None
        )

        root = QWidget()
        self.setWidget(root)

        layout_root = QVBoxLayout(root)
        layout_root.setContentsMargins(6, 6, 6, 6)
        layout_root.setSpacing(4)

        self.img_label_front = QLabel("⚙ Select CSV and image folder to start")
        self.img_label_back = QLabel("⚙ Select CSV and image folder to start")

        for lab in (self.img_label_front, self.img_label_back):
            lab.setAlignment(self._ALIGN_CENTER)
            lab.setMinimumSize(100, 150)
            lab.setScaledContents(False)
            lab.setSizePolicy(self._SIZEPOLICY_EXPANDING, self._SIZEPOLICY_EXPANDING)
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
            t.setAlignment(self._ALIGN_LEFT | self._ALIGN_VCENTER)
            t.setStyleSheet(f"font-weight:bold; color:{color}; font-size:11pt;")
            head.addWidget(t)

            inline_name_label.setAlignment(self._ALIGN_LEFT | self._ALIGN_VCENTER)
            inline_name_label.setText("—")
            inline_name_label.setToolTip("")
            inline_name_label.setSizePolicy(self._SIZEPOLICY_IGNORED, self._SIZEPOLICY_FIXED)
            inline_name_label.setMinimumWidth(80)
            inline_name_label.setWordWrap(False)
            inline_name_label.setTextInteractionFlags(self._TEXT_SELECTABLE_BY_MOUSE)

            head.addSpacing(8)
            head.addWidget(inline_name_label, 1)

            box.addLayout(head)
            box.addWidget(img_label, 1)
            return box

        img_area = QVBoxLayout()
        img_area.addLayout(
            _titled_box("Front", self.img_label_front, "#0078d7", self.inline_name_front), 1
        )
        img_area.addLayout(
            _titled_box("Back", self.img_label_back, "#d74100", self.inline_name_back), 1
        )

        btns_box = QVBoxLayout()
        btns_box.setContentsMargins(0, 0, 0, 0)
        btns_box.setSpacing(4)

        self.prev_btn = QPushButton("◀ Previous")
        self.next_btn = QPushButton("Next ▶")
        self.cfg_btn = QPushButton("⚙ Select Master Data")
        self.gmaps_btn = QPushButton("🌐 Street View")
        self.add_btn = QPushButton("● Add Mode")
        self.add_btn.setCheckable(True)
        self.add_btn.setToolTip("When ON, Clicking the map will add points to PhotoClicks")

        self.edit_btn = QPushButton("✎ Edit Mode")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip("When ON, Click to delete. Drag to move and re-assign attributes.")

        self.zoom_chk = QCheckBox("Auto Zoom")
        self.zoom_chk.setChecked(bool(auto_zoom_default))

        self.import_clicks_btn = QPushButton("⏯ Resume ")
        self.import_clicks_btn.setToolTip("Load previous click data and resume the session")

        self.export_clicks_btn = QPushButton("💾　Save ")
        self.export_clicks_btn.setToolTip("Save current clicks to a file")

        for b in (
            self.prev_btn, self.next_btn, self.cfg_btn, self.gmaps_btn,
            self.add_btn, self.edit_btn, self.import_clicks_btn, self.export_clicks_btn
        ):
            b.setSizePolicy(self._SIZEPOLICY_PREFERRED, self._SIZEPOLICY_FIXED)
            b.setMinimumWidth(60)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        for w in (
            self.prev_btn, self.next_btn, self.gmaps_btn,
            self.add_btn, self.edit_btn, self.zoom_chk
        ):
            row1.addWidget(w)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)
        for w in (self.cfg_btn, self.import_clicks_btn, self.export_clicks_btn):
            row2.addWidget(w)
        row2.addStretch(1)

        btns_box.addLayout(row1)
        btns_box.addLayout(row2)

        quick_area = QHBoxLayout()
        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("Jump by KP or image name.. Press Enter to jump")
        self.q_btn = QPushButton("Jump")
        quick_area.addWidget(self.q_edit, 1)
        quick_area.addWidget(self.q_btn)

        layout_root.addLayout(img_area, 1)
        layout_root.addLayout(btns_box, 0)
        layout_root.addLayout(quick_area, 0)

        self._apply_dynamic_button_text_color()

        QShortcut(QKeySequence(self._KEY_LEFT), self, activated=self.prevRequested.emit)
        QShortcut(QKeySequence(self._KEY_RIGHT), self, activated=self.nextRequested.emit)

        self.prev_btn.clicked.connect(self.prevRequested.emit)
        self.next_btn.clicked.connect(self.nextRequested.emit)
        self.cfg_btn.clicked.connect(self.configRequested.emit)
        self.gmaps_btn.clicked.connect(self.gmapsRequested.emit)
        self.add_btn.toggled.connect(self.addModeToggled.emit)
        self.edit_btn.toggled.connect(self.editModeToggled.emit)
        self.zoom_chk.toggled.connect(self.autoZoomToggled.emit)
        self.import_clicks_btn.clicked.connect(self.importClicksRequested.emit)
        self.export_clicks_btn.clicked.connect(self.exportClicksRequested.emit)
        self.q_btn.clicked.connect(lambda: self.jumpRequested.emit(self.q_edit.text().strip()))
        self.q_edit.returnPressed.connect(
            lambda: self.jumpRequested.emit(self.q_edit.text().strip())
        )

        iface.addDockWidget(self._RIGHT_DOCK, self)
        self.show()

    def _current_background_lightness(self) -> int:
        pal = self.palette() or QApplication.instance().palette()
        return pal.window().color().lightness()

    def _pick_button_text_color(self) -> str:
        return "#000" if self._current_background_lightness() > 128 else "#fff"

    def _apply_dynamic_button_text_color(self):
        root = self.widget()
        if root is None:
            return

        text_color = self._pick_button_text_color()
        root.setStyleSheet(f"""
        QPushButton {{ color: {text_color}; }}
        QPushButton:checked {{ color: {text_color}; }}
        QPushButton:hover {{ color: {text_color}; }}
        QPushButton:disabled {{ color: #888; }}
        """)

        if hasattr(self, "inline_name_front"):
            self.inline_name_front.setStyleSheet(
                f"color:{text_color}; font-family: Menlo, 'Courier New', monospace; font-size:10px;"
            )
        if hasattr(self, "inline_name_back"):
            self.inline_name_back.setStyleSheet(
                f"color:{text_color}; font-family: Menlo, 'Courier New', monospace; font-size:10px;"
            )

    def changeEvent(self, ev):
        event_types = tuple(
            x for x in (
                self._EVENT_PALETTE_CHANGE,
                self._EVENT_APP_PALETTE_CHANGE,
                self._EVENT_STYLE_CHANGE,
            )
            if x is not None
        )

        if ev.type() in event_types:
            self._apply_dynamic_button_text_color()

        super().changeEvent(ev)

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

    def setEditButtonChecked(self, checked: bool):
        self.edit_btn.setChecked(bool(checked))

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

        lab.setPixmap(
            pm.scaledToWidth(max(1, lab.width()), self._SMOOTH_TRANSFORM)
        )

        if not hasattr(lab, "_pv_orig_resizeEvent"):
            lab._pv_orig_resizeEvent = lab.resizeEvent

        def _resize(ev):
            cur = lab.pixmap()
            if cur and not cur.isNull():
                lab.setPixmap(
                    cur.scaledToWidth(max(1, lab.width()), self._SMOOTH_TRANSFORM)
                )
            if getattr(lab, "_pv_orig_resizeEvent", None):
                lab._pv_orig_resizeEvent(ev)

        lab.resizeEvent = _resize


def create_dock(auto_zoom_default: bool = True, iface=_iface) -> PhotoViewerDock:
    _ensure_singleton_dock(iface, PhotoViewerDock.OBJECT_NAME)
    return PhotoViewerDock(
        iface=iface,
        auto_zoom_default=auto_zoom_default,
        parent=iface.mainWindow()
    )
