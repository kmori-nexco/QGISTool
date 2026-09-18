# ui.py
from qgis.PyQt.QtCore import Qt, pyqtSignal, QEvent
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSizePolicy, QLineEdit, QCheckBox, QShortcut, QApplication
)
from qgis.utils import iface as _iface


def _qt_enum(container, scoped_name: str, legacy_name: str = None, default=None):
    #Qt5/Qt6 両対応で enum 値を取得する。
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
    categoryMasterRequested = pyqtSignal()

    OBJECT_NAME = "PhotoViewerDockPlus"

    def __init__(self, iface, auto_zoom_default: bool = True, parent=None):
        super().__init__("", parent or iface.mainWindow())
        self.setObjectName(self.OBJECT_NAME)

        # An empty native title bar still reserves vertical space. Replace it
        # with a zero-height widget so the bottom panel stays compact.
        self._title_bar = QWidget(self)
        self._title_bar.setFixedHeight(0)
        self.setTitleBarWidget(self._title_bar)

        # ---- Qt5/Qt6 互換 enum 吸収 ----
        self._BOTTOM_DOCK = _qt_enum(
            Qt, "DockWidgetArea.BottomDockWidgetArea", "BottomDockWidgetArea", 8
        )

        self._KEY_LEFT = _qt_enum(
            Qt, "Key.Key_Left", "Key_Left", 0x01000012
        )
        self._KEY_RIGHT = _qt_enum(
            Qt, "Key.Key_Right", "Key_Right", 0x01000014
        )

        self._SIZEPOLICY_FIXED = _qt_enum(
            QSizePolicy, "Policy.Fixed", "Fixed"
        )
        self._SIZEPOLICY_EXPANDING = _qt_enum(
            QSizePolicy, "Policy.Expanding", "Expanding"
        )
        self._SIZEPOLICY_PREFERRED = _qt_enum(
            QSizePolicy, "Policy.Preferred", "Preferred"
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
        root.setSizePolicy(self._SIZEPOLICY_EXPANDING, self._SIZEPOLICY_FIXED)
        self.setWidget(root)

        layout_root = QVBoxLayout(root)
        layout_root.setContentsMargins(6, 6, 6, 6)
        layout_root.setSpacing(4)

        btns_box = QVBoxLayout()
        btns_box.setContentsMargins(0, 0, 0, 0)
        btns_box.setSpacing(4)

        self.prev_btn = QPushButton("◀ Previous")
        self.next_btn = QPushButton("Next ▶")
        self.cfg_btn = QPushButton("⚙ Select Master Data")
        self.cate_master_btn = QPushButton("🏷 Category Master")
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
            self.prev_btn, self.next_btn, self.cfg_btn, self.cate_master_btn, self.gmaps_btn,
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
        for w in (self.cfg_btn, self.cate_master_btn, self.import_clicks_btn, self.export_clicks_btn):
            row2.addWidget(w)

        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("Jump by KP or image name.. Press Enter to jump")
        self.q_btn = QPushButton("Jump")
        row2.addSpacing(12)
        row2.addWidget(self.q_edit, 1)
        row2.addWidget(self.q_btn)

        btns_box.addLayout(row1)
        btns_box.addLayout(row2)

        layout_root.addLayout(btns_box, 0)

        self._apply_dynamic_button_text_color()

        QShortcut(QKeySequence(self._KEY_LEFT), self, activated=self.prevRequested.emit)
        QShortcut(QKeySequence(self._KEY_RIGHT), self, activated=self.nextRequested.emit)

        self.prev_btn.clicked.connect(self.prevRequested.emit)
        self.next_btn.clicked.connect(self.nextRequested.emit)
        self.cfg_btn.clicked.connect(self.configRequested.emit)
        self.cate_master_btn.clicked.connect(self.categoryMasterRequested.emit)
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

        self.setSizePolicy(self._SIZEPOLICY_EXPANDING, self._SIZEPOLICY_FIXED)
        compact_height = self.sizeHint().height()
        if compact_height > 0:
            self.setMaximumHeight(compact_height)
        iface.addDockWidget(self._BOTTOM_DOCK, self)
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

    def setAddButtonChecked(self, checked: bool):
        self.add_btn.setChecked(bool(checked))

    def setEditButtonChecked(self, checked: bool):
        self.edit_btn.setChecked(bool(checked))

    def setAutoZoomChecked(self, checked: bool):
        self.zoom_chk.setChecked(bool(checked))

def create_dock(auto_zoom_default: bool = True, iface=_iface) -> PhotoViewerDock:
    _ensure_singleton_dock(iface, PhotoViewerDock.OBJECT_NAME)
    return PhotoViewerDock(
        iface=iface,
        auto_zoom_default=auto_zoom_default,
        parent=iface.mainWindow()
    )
