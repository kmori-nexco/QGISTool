from typing import Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QPixmap, QKeySequence
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QLabel, QSizePolicy, QHBoxLayout,
    QPushButton, QLineEdit, QCheckBox, QShortcut
)
from qgis.utils import iface


class PhotoViewerUI(QWidget):
    # UI→ロジック のイベント
    sigPrev = pyqtSignal()
    sigNext = pyqtSignal()
    sigConfigure = pyqtSignal()
    sigToggleAdd = pyqtSignal()
    sigToggleDel = pyqtSignal()
    sigJump = pyqtSignal(str)
    sigAutoZoomToggled = pyqtSignal(bool)
    sigExportClicks = pyqtSignal()
    sigImageDblClicked = pyqtSignal(str)  # "front" | "back"
    sigResized = pyqtSignal(str)          # "front" | "back"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()

    # ---- public (controllerから操作) ----
    def set_front_pixmap(self, pix: Optional[QPixmap], fallback_text: str = ""):
        if pix:
            w = max(1, self.img_label_front.width())
            self.img_label_front.setPixmap(pix.scaledToWidth(w, Qt.SmoothTransformation))
        else:
            self.img_label_front.setPixmap(QPixmap())
            self.img_label_front.setText(fallback_text)

    def set_back_pixmap(self, pix: Optional[QPixmap], fallback_text: str = ""):
        if pix:
            w = max(1, self.img_label_back.width())
            self.img_label_back.setPixmap(pix.scaledToWidth(w, Qt.SmoothTransformation))
        else:
            self.img_label_back.setPixmap(QPixmap())
            self.img_label_back.setText(fallback_text)

    def set_name_labels(self, front_name: str, front_tip: str, back_name: str, back_tip: str, kp_text: str):
        self.name_label_front.setText(f"{front_name or '—'}  (KP:{kp_text})")
        self.name_label_front.setToolTip(front_tip or "")
        self.name_label_back.setText(f"{back_name or '—'}  (KP:{kp_text})")
        self.name_label_back.setToolTip(back_tip or "")

    def setBusy(self, busy: bool):
        self.setEnabled(not busy)

    def query_text(self) -> str:
        return self.q_edit.text().strip()

    def set_autozoom_checked(self, checked: bool):
        self.zoom_chk.setChecked(checked)

    def dockwidget(self) -> QDockWidget:
        return self.dock

    # ---- UI構築 ----
    def _build_ui(self):
        # 既存Dockの掃除
        for w in iface.mainWindow().findChildren(QDockWidget):
            if w.objectName() == "PhotoViewerDockPlus":
                w.close()
                w.deleteLater()

        self.dock = QDockWidget("画像ビューア＋", iface.mainWindow())
        self.dock.setObjectName("PhotoViewerDockPlus")

        root = QWidget()
        layout_root = QVBoxLayout(root)
        layout_root.setContentsMargins(6, 6, 6, 6)
        layout_root.setSpacing(4)

        # 画像ラベル
        self.img_label_front = QLabel("⚙ でCSVと画像フォルダを選択してください")
        self.img_label_back = QLabel("⚙ でCSVと画像フォルダを選択してください")
        for lab, side in ((self.img_label_front, "front"), (self.img_label_back, "back")):
            lab.setAlignment(Qt.AlignCenter)
            lab.setMinimumSize(420, 280)
            lab.setScaledContents(False)
            lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            lab.setStyleSheet("border: 1px solid #999; background-color:#fdfdfd;")
            
            def _dbl_handler(ev, s=side, lbl=lab):
                self.sigImageDblClicked.emit(s) 
                QLabel.mouseDoubleClickEvent(lbl, ev) 
        
            def _resize_handler(ev, s=side, lbl=lab):
                QLabel.resizeEvent(lbl, ev)
                self.sigResized.emit(s)

            lab.mouseDoubleClickEvent = _dbl_handler
            lab.resizeEvent = _resize_handler

        def titled_box(title, label, color):
            box = QVBoxLayout()
            t = QLabel(title)
            t.setAlignment(Qt.AlignCenter)
            t.setStyleSheet(f"font-weight:bold; color:{color}; font-size:11pt;")
            box.addWidget(t)
            box.addWidget(label, 1)
            return box

        img_area = QVBoxLayout()
        img_area.addLayout(titled_box("Front（前方）", self.img_label_front, "#0078d7"), 1)
        img_area.addLayout(titled_box("Back（後方）", self.img_label_back, "#d74100"), 1)

        # ファイル名 / KP
        self.name_label_front = QLabel("—")
        self.name_label_back = QLabel("—")
        for lab in (self.name_label_front, self.name_label_back):
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet("color:#888; font-family: Menlo, 'Courier New', monospace; font-size:10px; padding:2px;")
            lab.setMaximumHeight(18)
            lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        names_area = QVBoxLayout()
        names_area.addWidget(self.name_label_front)
        names_area.addWidget(self.name_label_back)

        # 操作列
        btns = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 前へ")
        self.next_btn = QPushButton("次へ ▶")
        self.cfg_btn = QPushButton("⚙ 設定")
        self.add_btn = QPushButton("● クリック追加"); self.add_btn.setCheckable(True)
        self.add_btn.setToolTip("ONにすると、地図クリックでPhotoClicksにポイントを追加します")
        self.del_btn = QPushButton("✖ クリック削除"); self.del_btn.setCheckable(True)
        self.del_btn.setToolTip("ONにすると、地図クリックでPhotoClicksのポイントを削除します")
        self.zoom_chk = QCheckBox("選択時に自動ズーム")
        self.export_clicks_btn = QPushButton("⬇ Clicks CSV保存")

        for b in (self.prev_btn, self.next_btn, self.cfg_btn, self.add_btn, self.del_btn, self.export_clicks_btn):
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        for w in (self.prev_btn, self.next_btn, self.cfg_btn, self.add_btn, self.del_btn, self.zoom_chk, self.export_clicks_btn):
            btns.addWidget(w)

        # クイック検索
        quick_area = QHBoxLayout()
        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("KP または 画像名でジャンプ… Enter で確定")
        self.q_btn = QPushButton("移動")
        quick_area.addWidget(self.q_edit, 1)
        quick_area.addWidget(self.q_btn)

        # レイアウト合成
        layout_root.addLayout(img_area, 1)
        layout_root.addLayout(names_area, 0)
        layout_root.addLayout(btns, 0)
        layout_root.addLayout(quick_area, 0)

        self.dock.setWidget(root)
        iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.show()

        # ショートカット
        QShortcut(QKeySequence(Qt.Key_Left), root, activated=self.sigPrev.emit)
        QShortcut(QKeySequence(Qt.Key_Right), root, activated=self.sigNext.emit)

        # クリック/変更イベント配線
        self.prev_btn.clicked.connect(self.sigPrev.emit)
        self.next_btn.clicked.connect(self.sigNext.emit)
        self.cfg_btn.clicked.connect(self.sigConfigure.emit)
        self.add_btn.clicked.connect(self.sigToggleAdd.emit)
        self.del_btn.clicked.connect(self.sigToggleDel.emit)
        self.q_btn.clicked.connect(lambda: self.sigJump.emit(self.query_text()))
        self.q_edit.returnPressed.connect(lambda: self.sigJump.emit(self.query_text()))
        self.zoom_chk.toggled.connect(self.sigAutoZoomToggled.emit)
        self.export_clicks_btn.clicked.connect(self.sigExportClicks.emit)

        # UI初期状態
        self.set_front_pixmap(None, "CSVが未読み込みです。⚙設定から指定してください。")
        self.set_back_pixmap(None, "CSVが未読み込みです。⚙設定から指定してください。")

    def set_add_mode_text(self, on: bool):
        self.add_btn.setText("● クリック追加（ON）" if on else "● クリック追加")
        self.add_btn.setChecked(on)

    def set_del_mode_text(self, on: bool):
        self.del_btn.setText("✖ クリック削除（ON）" if on else "✖ クリック削除")
        self.del_btn.setChecked(on)
        
