from typing import Optional

from qgis.PyQt.QtGui import QColor
from .viewer_ui import PhotoViewerUI
from .viewer_qgis import QgisService
from .viewer_logic import PhotoViewerController


class PhotoViewerPlus:

    def __init__(self):
        self._ui = PhotoViewerUI()
        self._svc = QgisService()
        self._ctrl = PhotoViewerController(self._ui, self._svc)

    # ---- 互換の公開メソッド（必要に応じて増やす）----
    def show_image(self, idx: int):
        self._ctrl.show_image(idx)

    def next_image(self):
        self._ctrl.next_image()

    def prev_image(self):
        self._ctrl.prev_image()

    def configure_and_load(self):
        self._ctrl.configure_and_load()
