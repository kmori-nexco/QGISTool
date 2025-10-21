from pathlib import Path
from qgis.gui import QgsMapTool
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsCoordinateTransform, QgsProject, QgsGeometry, QgsPointXY, QgsFeature
)
from .utils import EditContext

class AddPointTool(QgsMapTool):
    def __init__(self, owner, canvas, target_layer):
        super().__init__(canvas)
        self.owner = owner
        self.canvas = canvas
        self.target = target_layer
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, event):
        if not self.target or not self.target.isValid():
            QMessageBox.warning(self.canvas, "PhotoClicks", "ターゲットレイヤが無効です。"); return
        map_crs = self.canvas.mapSettings().destinationCrs()
        layer_crs = self.target.crs()
        try:
            xform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
            pt_layer = xform.transform(event.mapPoint())
        except Exception as e:
            QMessageBox.warning(self.canvas, "PhotoClicks", f"座標変換に失敗: {e}"); return

        extra_attrs = self.owner._prompt_attributes()
        jpg_val = ""
        try:
            if self.owner.images:
                jpg_val = Path(self.owner.images[self.owner.current_index].front or "").name
        except Exception:
            pass

        try:
            # 任意属性フィールドを保証
            names = set(self.target.fields().names())
            need = [k for k in extra_attrs.keys() if k not in names]
            if need:
                with EditContext(self.target):
                    from qgis.PyQt.QtCore import QVariant
                    from qgis.core import QgsField
                    self.target.dataProvider().addAttributes([QgsField(k, QVariant.String) for k in need])
                    self.target.updateFields()

            if not self.target.isEditable():
                self.target.startEditing()

            f = QgsFeature(self.target.fields())
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt_layer.x(), pt_layer.y())))

            ilat = self.target.fields().indexFromName("lat")
            ilon = self.target.fields().indexFromName("lon")
            ijpg = self.target.fields().indexFromName("jpg")
            if ilat >= 0: f.setAttribute(ilat, float(pt_layer.y()))
            if ilon >= 0: f.setAttribute(ilon, float(pt_layer.x()))
            if ijpg >= 0: f.setAttribute(ijpg, jpg_val)

            for k, v in extra_attrs.items():
                idx = self.target.fields().indexFromName(k)
                if idx >= 0:
                    f.setAttribute(idx, v)

            ok = self.target.dataProvider().addFeatures([f])
            if ok:
                self.target.commitChanges(); self.target.triggerRepaint()
            else:
                self.target.rollBack()
                QMessageBox.warning(self.canvas, "PhotoClicks", "ポイントの追加に失敗しました。")
        except Exception as e:
            try: self.target.rollBack()
            except Exception: pass
            QMessageBox.critical(self.canvas, "PhotoClicks", f"追加時エラー: {e}")

class DeletePointTool(QgsMapTool):
    def __init__(self, owner, canvas, target_layer):
        super().__init__(canvas)
        self.owner = owner
        self.canvas = canvas
        self.target = target_layer
        self.setCursor(Qt.ForbiddenCursor)

    def canvasReleaseEvent(self, event):
        if not self.target or not self.target.isValid():
            QMessageBox.warning(self.canvas, "PhotoClicks", "ターゲットレイヤが無効です。"); return

        pt_map = event.mapPoint()
        mpp = self.canvas.mapSettings().mapUnitsPerPixel()
        tol_map = mpp * 10

        layer_to_map = QgsCoordinateTransform(self.target.crs(),
                                              self.canvas.mapSettings().destinationCrs(),
                                              QgsProject.instance())

        pt_geom_map = QgsGeometry.fromPointXY(QgsPointXY(pt_map.x(), pt_map.y()))

        nearest_f = None
        nearest_dist = None
        for f in self.target.getFeatures():
            try:
                geom_map = QgsGeometry(f.geometry())
                geom_map.transform(layer_to_map)
                d = pt_geom_map.distance(geom_map)
                if nearest_dist is None or d < nearest_dist:
                    nearest_dist = d; nearest_f = f
            except Exception:
                pass

        if nearest_f is None or nearest_dist is None or nearest_dist > tol_map:
            return

        try: jpg_val = str(nearest_f["jpg"] or "")
        except Exception: jpg_val = ""
        reply = QMessageBox.question(
            self.canvas, "PhotoClicks",
            f"このポイントを削除しますか？\n(jpg: {jpg_val}, fid: {nearest_f.id()})",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            with EditContext(self.target):
                if not self.target.dataProvider().deleteFeatures([nearest_f.id()]):
                    raise Exception("deleteFeatures が失敗")
            self.target.triggerRepaint()
        except Exception as e:
            QMessageBox.critical(self.canvas, "PhotoClicks", f"削除時エラー: {e}")
