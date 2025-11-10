#maptools.py
from pathlib import Path
from typing import Optional, Tuple, Type
from qgis.gui import QgsMapTool, QgsMapCanvas
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox
try:
    # PyQt6
    from qgis.PyQt.QtGui import QCursor, QPixmap
except Exception:
    QCursor = None
    QPixmap = None
from qgis.core import (
    QgsCoordinateTransform, QgsProject, QgsGeometry, QgsPointXY, QgsFeature
)
from .utils import EditContext
from .fields import FN, apply_schema, normalize_category, clear_unrelated_category_attrs, MAIN_TO_SUBFIELD

class AddPointTool(QgsMapTool):
    def __init__(self, owner, canvas, target_layer):
        super().__init__(canvas)
        self.owner = owner
        self.canvas = canvas
        self.target = target_layer
        # PyQt5/6 互換: CursorShape 名前空間と QCursor 有無を吸収
        CursorEnum = getattr(Qt, "CursorShape", Qt)
        cross = getattr(CursorEnum, "CrossCursor", Qt.CrossCursor)
        if QCursor:
            self.setCursor(QCursor(cross))
        else:
            self.setCursor(cross)

    def canvasReleaseEvent(self, event):
        if not self.target or not self.target.isValid():
            QMessageBox.warning(self.canvas, "PhotoClicks", "Target layer is invalid")
            return
        
        try:
            apply_schema(self.target)
        except Exception as e:
            QMessageBox.warning(self.canvas, "PhotoClicks", f"Failed to apply schema: {e}")
            return

        # 座標変換
        map_crs = self.canvas.mapSettings().destinationCrs()
        layer_crs = self.target.crs()
        try:
            xform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
            pt_layer = xform.transform(event.mapPoint())
        except Exception as e:
            QMessageBox.warning(self.canvas, "PhotoClicks", f"Coordinate transformation failed: {e}")
            return

        # viewer側で複数レコードを返すようにしたので list の可能性あり
        extra_attrs_list = self.owner._prompt_attributes()
        if not extra_attrs_list:
            return
        if isinstance(extra_attrs_list, dict):
            extra_attrs_list = [extra_attrs_list]

        # いま表示してる画像名を拾う（frontを優先）
        jpg_val = ""
        try:
            if self.owner.images:
                jpg_val = Path(self.owner.images[self.owner.current_index].front or "").name
        except Exception:
            pass

        try:
            if not self.target.isEditable():
                self.target.startEditing()

            for extra_attrs in extra_attrs_list:
                names_now = set(self.target.fields().names())
                need = [k for k in extra_attrs.keys() if k not in names_now]
                if need:
                    with EditContext(self.target):
                        from qgis.PyQt.QtCore import QVariant
                        from qgis.core import QgsField
                        self.target.dataProvider().addAttributes([QgsField(k, QVariant.String) for k in need])
                        self.target.updateFields()

                # フィーチャ作成
                f = QgsFeature(self.target.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt_layer.x(), pt_layer.y())))

                # 座標・画像
                ilat = self.target.fields().indexFromName(FN.LAT)
                ilon = self.target.fields().indexFromName(FN.LON)
                ijpg = self.target.fields().indexFromName(FN.JPG)
                if ilat >= 0:
                    f.setAttribute(ilat, float(pt_layer.y()))
                if ilon >= 0:
                    f.setAttribute(ilon, float(pt_layer.x()))
                if ijpg >= 0:
                    f.setAttribute(ijpg, jpg_val)

                # まずは選択された属性をそのままセット
                for k, v in extra_attrs.items():
                    idx = self.target.fields().indexFromName(k)
                    if idx >= 0:
                        f.setAttribute(idx, v)

                # カテゴリに応じた Null 化（ロジックは fields.py に集約）
                raw_category = extra_attrs.get(FN.CATEGORY) or extra_attrs.get("category") or ""
                category_norm = normalize_category(raw_category)
                clear_unrelated_category_attrs(self.target, f, category_norm)

                if not self.target.addFeatures([f]):
                    raise Exception("addFeatures failed.")

            self.target.commitChanges()
            self.target.triggerRepaint()

        except Exception as e:
            try:
                self.target.rollBack()
            except Exception:
                pass
            QMessageBox.critical(self.canvas, "PhotoClicks", f"Error while adding point(s): {e}")


class DeletePointTool(QgsMapTool):
    TOL_PIXELS = 10
    def __init__(self, owner, canvas, target_layer):
        super().__init__(canvas)
        self.owner = owner
        self.canvas = canvas
        self.target = target_layer

        from pathlib import Path
        cursor_path = Path(__file__).parent / "icons" / "deletemode_cursor.png"
        print(f"[PhotoClicks] Cursor path exists? {cursor_path.exists()}  ({cursor_path})")

        try:
            if QPixmap and cursor_path.exists():
                pm = QPixmap(str(cursor_path))
                if not pm.isNull():
                    max_size = 24 # ゴミ箱のサイズ
                    pm = pm.scaled(
                        max_size, max_size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )

                    #クリック座標（0なら画像の左上に設定）
                    hot_x = 0
                    hot_y = 0
                    if QCursor:
                        self.setCursor(QCursor(pm, hot_x, hot_y))
                        return

        except Exception as e:
            print(f"[PhotoClicks] Custom cursor load failed: {e}")

        # ここまで来たらフォールバック：十字カーソル
        CursorEnum = getattr(Qt, "CursorShape", Qt)
        cross = getattr(CursorEnum, "CrossCursor", Qt.CrossCursor)
        if QCursor:
            self.setCursor(QCursor(cross))
        else:
            self.setCursor(cross)
        print("[PhotoClicks] Fallback CrossCursor used.")

    def canvasReleaseEvent(self, event):
        if not self.target or not self.target.isValid():
            QMessageBox.warning(self.canvas, "PhotoClicks", "Target layer is invalid")
            return

        pt_map = event.mapPoint()
        mpp = self.canvas.mapSettings().mapUnitsPerPixel()
        tol_map = mpp * self.TOL_PIXELS

        layer_to_map = QgsCoordinateTransform(
            self.target.crs(),
            self.canvas.mapSettings().destinationCrs(),
            QgsProject.instance()
        )

        pt_geom_map = QgsGeometry.fromPointXY(QgsPointXY(pt_map.x(), pt_map.y()))

        nearest_f = None
        nearest_dist = None
        for f in self.target.getFeatures():
            try:
                geom_map = QgsGeometry(f.geometry())
                geom_map.transform(layer_to_map)
                d = pt_geom_map.distance(geom_map)
                if nearest_dist is None or d < nearest_dist:
                    nearest_dist = d
                    nearest_f = f
            except Exception:
                pass

        if nearest_f is None or nearest_dist is None or nearest_dist > tol_map:
            return

        try:
            jpg_val = str(nearest_f[FN.JPG] or "")
        except Exception:
            jpg_val = ""

        def _get_attr_safe(feat, name, default=""):
            try:
                idx = feat.fields().indexOf(name)
                if idx < 0:
                    return default
                v = feat[name]
                return "" if v is None else str(v)
            except Exception:
                return default

        parent_raw  = _get_attr_safe(nearest_f, FN.CATEGORY)
        parent_norm = normalize_category(parent_raw) or ""
        parent_disp = parent_norm or "—"

        sub_field = MAIN_TO_SUBFIELD.get(parent_norm) or MAIN_TO_SUBFIELD.get(parent_raw)
        child_val = _get_attr_safe(nearest_f, sub_field, "") if sub_field else ""

        if not child_val:
            for nm in ("subcat", FN.TRAFFIC_SIGN, FN.POLE, FN.FIREHYDRANT):
                v = _get_attr_safe(nearest_f, nm, "")
                if v and v.lower() != "combined":
                    child_val = v
                    break

        child_disp = child_val or "—"

        # PyQt5/6 互換: StandardButton を優先して取得
        _StdBtn = getattr(QMessageBox, "StandardButton", QMessageBox)
        _YES = getattr(_StdBtn, "Yes", QMessageBox.Yes)
        _NO  = getattr(_StdBtn, "No", QMessageBox.No)
        reply = QMessageBox.question(
            self.canvas,
            "PhotoClicks",
            f"Do you want to delete this point?\n"
            f"(jpg: {jpg_val}, fid: {nearest_f.id()})\n"
            f"Parent category: {parent_disp}\n"
            f"Subcategory: {child_disp}",
            _YES | _NO,
            _NO
        )
        if reply != _YES:
            return

        try:
            with EditContext(self.target):
                if not self.target.dataProvider().deleteFeatures([nearest_f.id()]):
                    raise Exception("deleteFeatures failed")
            self.target.triggerRepaint()
        except Exception as e:
            QMessageBox.critical(self.canvas, "PhotoClicks", f"Error while deleting point(s): {e}")

#AddPointTool
def enable_add_mode(owner, canvas: QgsMapCanvas, target_layer) -> Tuple[Optional[QgsMapTool], AddPointTool]:
    if not target_layer or not target_layer.isValid():
        raise ValueError("enable_add_mode: target_layer is invalid")
    prev = canvas.mapTool()
    tool = AddPointTool(owner, canvas, target_layer)
    canvas.setMapTool(tool)
    return prev, tool

#DeletePointTool
def enable_del_mode(owner, canvas: QgsMapCanvas, target_layer) -> Tuple[Optional[QgsMapTool], DeletePointTool]:
    if not target_layer or not target_layer.isValid():
        raise ValueError("enable_del_mode: target_layer is invalid")
    prev = canvas.mapTool()
    tool = DeletePointTool(owner, canvas, target_layer)
    canvas.setMapTool(tool)
    return prev, tool

#DisableCurrentTool
def disable_current_tool(canvas: QgsMapCanvas, prev_tool: Optional[QgsMapTool]) -> None:
    try:
        if prev_tool is not None:
            canvas.setMapTool(prev_tool)
    except Exception:
        # 復帰に失敗しても致命的ではないので握りつぶす
        pass

def is_tool_active(canvas: QgsMapCanvas, tool: Optional[QgsMapTool]) -> bool:
    """現在のツールが `tool` かどうかを返す（念のためのチェック用）"""
    try:
        return tool is not None and canvas.mapTool() is tool
    except Exception:
        return False

def enable_tool(owner, canvas: QgsMapCanvas, target_layer, tool_cls: Type[QgsMapTool]
            ) -> Tuple[Optional[QgsMapTool], QgsMapTool]:
    """指定した tool_cls を有効化して (prev_tool, new_tool) を返す"""
    if not target_layer or not target_layer.isValid():
        raise ValueError("enable_tool: target_layer is invalid")
    prev = canvas.mapTool()
    tool = tool_cls(owner, canvas, target_layer)
    canvas.setMapTool(tool)
    return prev, tool

def _set_btn(btn, text, checked):
    # setText と setChecked の間でもう一度トグルが飛ぶ UI もあるので、まとめてブロック
    prev = btn.blockSignals(True)
    try:
        btn.setText(text)
        btn.setChecked(checked)
    finally:
        btn.blockSignals(prev)

def toggle_tool_mode(
    owner, canvas: QgsMapCanvas, target_layer, current_tool_attr: str,
    prev_tool_attr: str, tool_cls: Type[QgsMapTool],
    on_label: str, off_label: str, btn,
    conflict: Optional[Tuple[str, str, str, str, object]] = None,
    # conflict = (conflict_current_tool_attr, conflict_prev_tool_attr, conflict_off_label, conflict_btn_attr, iface_or_none)
):
    cur_tool = getattr(owner, current_tool_attr, None)

    if cur_tool:
        # OFF 処理
        disable_current_tool(canvas, getattr(owner, prev_tool_attr, None))
        setattr(owner, current_tool_attr, None)
        setattr(owner, prev_tool_attr, None)
        _set_btn(btn, off_label, False)
        return

    # ON 処理（必要なら競合を先にOFF）
    if conflict:
        conflict_tool_attr, conflict_prev_attr, conflict_off_label, conflict_btn_attr, _ = conflict
        if getattr(owner, conflict_tool_attr, None):
            disable_current_tool(canvas, getattr(owner, conflict_prev_attr, None))
            setattr(owner, conflict_tool_attr, None)
            setattr(owner, conflict_prev_attr, None)
            conflict_btn = getattr(owner, conflict_btn_attr)
            _set_btn(conflict_btn, conflict_off_label, False)

    if not target_layer or not target_layer.isValid():
        _set_btn(btn, off_label, False)

    prev, tool = enable_tool(owner, canvas, target_layer, tool_cls)
    setattr(owner, current_tool_attr, tool)
    setattr(owner, prev_tool_attr, prev)
    _set_btn(btn, on_label, True) 
