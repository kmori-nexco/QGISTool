from typing import Optional, Iterable, List, Tuple, Dict
from pathlib import Path

from qgis.PyQt.QtCore import Qt, QUrl, QVariant, QStandardPaths
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox, QProgressDialog
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsMapLayer, QgsField
)
from qgis.utils import iface

from . import layers as lyrmod
from . import symbology as symb
from .utils import export_layer_to_csv


class QgisService:
    """QGIS/iface依存の操作をここに集約。ロジックからはこの薄いAPIを叩くだけにする。"""

    def set_canvas_selection_color(self, color: QColor):
        iface.mapCanvas().setSelectionColor(color)

    # ---- Dock/Canvas ----
    def add_dockwidget(self, dock):
        iface.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.show()

    def canvas(self):
        return iface.mapCanvas()

    # ---- レイヤ確保・初期化 ----
    def ensure_point_layer(self, name: str) -> QgsVectorLayer:
        lyr = lyrmod.ensure_point_layer(name)
        symb.apply_plane_symbology(lyr)
        return lyr

    def ensure_click_layer(self, name: str) -> Optional[QgsVectorLayer]:
        # 既存検索
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == name and lyr.type() == QgsMapLayer.VectorLayer and lyr.geometryType() == QgsWkbTypes.PointGeometry:
                return lyr
        # 新規作成
        try:
            return lyrmod.ensure_click_layer(name)
        except Exception:
            QMessageBox.critical(iface.mainWindow(), "PhotoClicks", "クリック追加用レイヤの作成に失敗しました。")
            return None

    def project(self) -> QgsProject:
        return QgsProject.instance()

    # ---- 選択/ズーム ----
    def select_by_ids(self, layer: QgsVectorLayer, ids: List[int]):
        layer.removeSelection()
        if ids:
            layer.selectByIds(ids)

    def zoom_to_feature_ids(self, layer: QgsVectorLayer, ids: List[int], scale: float = 500):
        if not ids:
            return
        c = self.canvas()
        c.zoomToFeatureIds(layer, ids)
        c.zoomScale(scale)
        c.refresh()

    # ---- ファイルダイアログ/URL ----
    def pick_paths(self, last_csv: str, last_img: str) -> Tuple[str, str]:
        csv_file, _ = QFileDialog.getOpenFileName(
            iface.mainWindow(),
            "CSVを選択（kp,lat_kp,lon_kp,street,pic_front,lat_front,lon_front,course_front,pic_back,lat_back,lon_back,course_back）",
            last_csv, "CSV (*.csv)"
        )
        if not csv_file:
            raise Exception("CSVが選択されていません。")
        img_dir = QFileDialog.getExistingDirectory(iface.mainWindow(), "画像フォルダを選択", last_img)
        if not img_dir:
            raise Exception("画像フォルダが選択されていません。")
        return csv_file, img_dir

    def open_local_file(self, path: Path):
        from qgis.PyQt.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ---- 進捗 ----
    def progress_dialog(self, text: str) -> QProgressDialog:
        prog = QProgressDialog(text, "中止", 0, 0, iface.mainWindow())
        prog.setWindowModality(Qt.ApplicationModal)
        prog.setMinimumDuration(400)
        return prog

    # ---- CSV出力 ----
    def ask_export_csv_path(self, last_path: str) -> str:
        out_csv, _ = QFileDialog.getSaveFileName(
            iface.mainWindow(),
            "PhotoClicks を CSV に保存",
            last_path,
            "CSV (*.csv)"
        )
        return out_csv or ""

    def info(self, title: str, msg: str):
        QMessageBox.information(iface.mainWindow(), title, msg)

    def warn(self, title: str, msg: str):
        QMessageBox.warning(iface.mainWindow(), title, msg)

    def error(self, title: str, msg: str):
        QMessageBox.critical(iface.mainWindow(), title, msg)

    def export_layer_csv(self, lyr: QgsVectorLayer, path: str, only_selected: bool):
        export_layer_to_csv(lyr, path, only_selected=only_selected)
