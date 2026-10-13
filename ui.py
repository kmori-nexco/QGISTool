# ui.py
from qgis.PyQt.QtCore import Qt, pyqtSignal, QEvent
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSizePolicy, QApplication
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


class QGISToolDock(QDockWidget):
    streetViewModeToggled = pyqtSignal(bool)
    addModeToggled = pyqtSignal(bool)
    editModeToggled = pyqtSignal(bool)
    importClicksRequested = pyqtSignal()
    exportClicksRequested = pyqtSignal()
    categoryMasterRequested = pyqtSignal()

    OBJECT_NAME = "QGISToolDock"

    def __init__(self, iface, parent=None):
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

        self.cate_master_btn = QPushButton("🏷 Category Master")
        self.streetview_btn = QPushButton("🌐 Street View Mode")
        self.streetview_btn.setCheckable(True)
        self.streetview_btn.setToolTip("When ON, click the map to open Street View at that location")
        self.add_btn = QPushButton("● Add Mode")
        self.add_btn.setCheckable(True)
        self.add_btn.setToolTip("When ON, drag to pan the map; double-click to add a point to PhotoClicks")

        self.edit_btn = QPushButton("✎ Edit Mode")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip("When ON, Click to delete. Drag to move and re-assign attributes.")

        self.import_clicks_btn = QPushButton("⏯ Resume ")
        self.import_clicks_btn.setToolTip("Load previous click data and resume the session")

        self.export_clicks_btn = QPushButton("💾　Save ")
        self.export_clicks_btn.setToolTip("Save current clicks to a file")

        for b in (
            self.cate_master_btn, self.streetview_btn,
            self.add_btn, self.edit_btn, self.import_clicks_btn, self.export_clicks_btn
        ):
            b.setSizePolicy(self._SIZEPOLICY_PREFERRED, self._SIZEPOLICY_FIXED)
            b.setMinimumWidth(60)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)
        for w in (
            self.add_btn,
            self.edit_btn,
            self.streetview_btn,
            self.cate_master_btn,
            self.import_clicks_btn,
            self.export_clicks_btn,
        ):
            row1.addWidget(w)
        row1.addStretch(1)

        layout_root.addLayout(row1, 0)

        self._apply_dynamic_button_text_color()

        self.cate_master_btn.clicked.connect(self.categoryMasterRequested.emit)
        self.streetview_btn.toggled.connect(self.streetViewModeToggled.emit)
        self.add_btn.toggled.connect(self.addModeToggled.emit)
        self.edit_btn.toggled.connect(self.editModeToggled.emit)
        self.import_clicks_btn.clicked.connect(self.importClicksRequested.emit)
        self.export_clicks_btn.clicked.connect(self.exportClicksRequested.emit)

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

def create_dock(iface=_iface) -> QGISToolDock:
    _ensure_singleton_dock(iface, QGISToolDock.OBJECT_NAME)
    return QGISToolDock(
        iface=iface,
        parent=iface.mainWindow()
    )
