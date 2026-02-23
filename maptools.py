#maptools.py
import time
from pathlib import Path
from typing import Optional, Tuple, Type
from qgis.gui import QgsMapTool, QgsMapCanvas
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox
try:
    from qgis.PyQt.QtGui import QCursor, QPixmap
except Exception:
    QCursor = None
    QPixmap = None

from qgis.core import (
    QgsCoordinateTransform, QgsProject, QgsGeometry, QgsPointXY, QgsFeature
)

from .utils import EditContext
from .fields import FN, apply_schema, normalize_category, clear_unrelated_category_attrs

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

    def _has_same_coord_feature(self, pt_layer) -> bool:
        tol = getattr(self.owner, "COORD_TOL", 1e-7)
        x = float(pt_layer.x())
        y = float(pt_layer.y())

        try:
            for f in self.target.getFeatures():
                try:
                    g = f.geometry()
                    if not g:
                        continue
                    p = g.asPoint()
                    if abs(p.x() - x) <= tol and abs(p.y() - y) <= tol:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

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

        same_coord_exists = self._has_same_coord_feature(pt_layer)

        extra_attrs_list = self.owner._prompt_attributes()
        if not extra_attrs_list:
            return
        if isinstance(extra_attrs_list, dict):
            extra_attrs_list = [extra_attrs_list]

        will_be_combined = (len(extra_attrs_list) >= 2) or same_coord_exists

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
                # ★先に subcat 注入（フィールド追加判定より前）
                if will_be_combined and not (extra_attrs.get("subcat") or extra_attrs.get("subCategory")):
                    extra_attrs["subcat"] = "combined"

                # ★必要なフィールド追加（ここだけが if need の中）
                names_now = set(self.target.fields().names())
                need = [k for k in extra_attrs.keys() if k not in names_now]
                if need:
                    with EditContext(self.target):
                        from qgis.PyQt.QtCore import QVariant
                        from qgis.core import QgsField
                        self.target.dataProvider().addAttributes([QgsField(k, QVariant.String) for k in need])
                        self.target.updateFields()

                # ★フィーチャ作成（need の有無に関係なく毎回やる）
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

class EditPointTool(QgsMapTool):
    TOL_PIXELS = 10

    def __init__(self, owner, canvas, target_layer):
        super().__init__(canvas)
        self.owner = owner
        self.canvas = canvas
        self.target = target_layer

        # drag state
        self._press_pt_map = None
        self._drag_fid = None
        self._dragging = False
        self.last_preview_t = 0.0
        self._drag_start_layer_pt = None
        self._drag_start_fid = None 

        cursor_path = Path(__file__).parent / "icons" / "editmode_cursor.png"
        try:
            if QPixmap and cursor_path.exists():
                pm = QPixmap(str(cursor_path))
                if not pm.isNull():
                    pm = pm.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    if QCursor:
                        self.setCursor(QCursor(pm, 0, 0))
                        return
        except Exception:
            pass

        CursorEnum = getattr(Qt, "CursorShape", Qt)
        cross = getattr(CursorEnum, "CrossCursor", Qt.CrossCursor)
        self.setCursor(QCursor(cross) if QCursor else cross)

    def _same_coord_fids(self, fid: int, pt_layer) -> list:
        tol = getattr(self.owner, "COORD_TOL", 1e-7)
        x = float(pt_layer.x())
        y = float(pt_layer.y())
        hits = []
        try:
            for f in self.target.getFeatures():
                if f.id() == fid:
                    continue
                try:
                    p = f.geometry().asPoint()
                    if abs(p.x() - x) <= tol and abs(p.y() - y) <= tol:
                        hits.append(f.id())
                except Exception:
                    pass
        except Exception:
            pass
        return hits

    # ---- 共通：最近傍探索 ----
    def _nearest_feature(self, pt_map):
        if not self.target or not self.target.isValid():
            return None, None

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
            return None, None
        return nearest_f, nearest_dist

    def _map_to_layer_point(self, pt_map):
        map_crs = self.canvas.mapSettings().destinationCrs()
        layer_crs = self.target.crs()
        xform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        return xform.transform(pt_map)
    
    def canvasMoveEvent(self, event):
        if self._drag_fid is None or self._press_pt_map is None:
            return

        # まだ dragging 判定してないなら、一定距離で True にする
        if not self._dragging:
            dx = event.mapPoint().x() - self._press_pt_map.x()
            dy = event.mapPoint().y() - self._press_pt_map.y()
            if (dx*dx + dy*dy) > (self.canvas.mapSettings().mapUnitsPerPixel() * 3) ** 2:
                self._dragging = True
            else:
                return  # まだドラッグじゃない

        # ---- ここから「ドラッグ中のプレビュー移動」 ----
        # 更新頻度を間引き（重い場合の保険）
        now = time.time()
        if (now - self.last_preview_t) < 0.05:  # 50ms
            return
        self.last_preview_t = now

        try:
            pt_layer = self._map_to_layer_point(event.mapPoint())
            g = QgsGeometry.fromPointXY(QgsPointXY(pt_layer.x(), pt_layer.y()))

            with EditContext(self.target):
                try:
                    self.target.changeGeometry(self._drag_fid, g)
                except Exception:
                    self.target.dataProvider().changeGeometryValues({self._drag_fid: g})

                # lat/lonも追従更新（CSV出力/他処理が素直になる）
                ilat = self.target.fields().indexFromName(FN.LAT)
                ilon = self.target.fields().indexFromName(FN.LON)
                if ilat >= 0:
                    self.target.changeAttributeValue(self._drag_fid, ilat, float(pt_layer.y()))
                if ilon >= 0:
                    self.target.changeAttributeValue(self._drag_fid, ilon, float(pt_layer.x()))

            self.target.triggerRepaint()
        except Exception:
            pass

    # ---- クリック：属性（カテゴリ）だけ更新 ----
    def _set_attrs_only(self, fid: int) -> None:
        apply_schema(self.target)

        extra = self._prompt_single_attrs_for_edit()
        if not extra:
            return

        with EditContext(self.target):
            # 選ばれたキーだけ上書き
            for k, v in extra.items():
                idx = self.target.fields().indexFromName(k)
                if idx >= 0:
                    self.target.changeAttributeValue(fid, idx, v)

        # カテゴリに応じたNull化
        raw_category = (extra.get(FN.CATEGORY) if isinstance(extra, dict) else "") or ""
        category_norm = normalize_category(raw_category)

        f = next(self.target.getFeatures(f"id={fid}"), None)
        if f is not None:
            clear_unrelated_category_attrs(self.target, f, category_norm)
            with EditContext(self.target):
                # ここは「カテゴリに紐づく列」を必要に応じて増やしてOK
                for cand in (FN.TRAFFIC_SIGN, FN.POLE, FN.FIREHYDRANT, FN.UNKNOWN):
                    idxc = self.target.fields().indexFromName(cand)
                    if idxc >= 0:
                        self.target.changeAttributeValue(fid, idxc, f[cand])

        self.target.triggerRepaint()

    # ---- 削除（クリック時） ----
    def _delete_feature_with_confirm(self, feat: QgsFeature):
        try:
            jpg_val = str(feat[FN.JPG] or "")
        except Exception:
            jpg_val = ""

        _StdBtn = getattr(QMessageBox, "StandardButton", QMessageBox)
        _YES = getattr(_StdBtn, "Yes", QMessageBox.Yes)
        _NO  = getattr(_StdBtn, "No", QMessageBox.No)
        reply = QMessageBox.question(
            self.canvas,
            "PhotoClicks",
            f"Delete this point?\n(jpg: {jpg_val}, fid: {feat.id()})",
            _YES | _NO,
            _NO
        )
        if reply != _YES:
            return

        with EditContext(self.target):
            if not self.target.dataProvider().deleteFeatures([feat.id()]):
                raise Exception("deleteFeatures failed")
        self.target.triggerRepaint()

    def _prompt_single_attrs_for_edit(self) -> Optional[dict]:
        #Edit(移動確定)用：属性セットは必ず1つだけ。複数だったら警告して選び直し
        while True:
            extra = self.owner._prompt_attributes()
            if not extra:
                return None

            if isinstance(extra, dict):
                return extra

            if isinstance(extra, list):
                if len(extra) == 1 and isinstance(extra[0], dict):
                    return extra[0]

                QMessageBox.information(
                    self.canvas,
                    "PhotoClicks",
                    "Edit mode updates ONE point at a time.\nPlease select exactly one attribute set."
                )
                continue
            return None

    # ---- events ----
    def canvasPressEvent(self, event):
        if not self.target or not self.target.isValid():
            return
        
        if event.button() == Qt.RightButton:
            self._press_pt_map = None
            self._drag_fid = None
            self._dragging = False
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            return
        
        self._press_pt_map = event.mapPoint()
        self._dragging = False

        feat, _ = self._nearest_feature(self._press_pt_map)
        self._drag_fid = feat.id() if feat else None

        # ★移動前座標（layer CRS）を保存
        self._drag_start_layer_pt = None
        self._drag_start_fid = self._drag_fid
        if feat is not None:
            try:
                p = feat.geometry().asPoint()
                self._drag_start_layer_pt = QgsPointXY(float(p.x()), float(p.y()))
            except Exception:
                pass

    def canvasReleaseEvent(self, event):
        if not self.target or not self.target.isValid():
            return

        # 右クリック：削除（ドラッグ中は無視）→ fidが無くても release地点で探す
        if (not self._dragging) and (event.button() == Qt.RightButton):
            feat, _ = self._nearest_feature(event.mapPoint())
            if feat:
                try:
                    self._delete_feature_with_confirm(feat)
                except Exception as e:
                    QMessageBox.critical(self.canvas, "PhotoClicks", f"Delete failed: {e}")
            # 状態クリア
            self._drag_fid = None
            self._press_pt_map = None
            self._dragging = False
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            return

        # ここから先（左クリック/ドラッグ）は press 時に掴めてないなら何もしない
        if self._drag_fid is None:
            self._press_pt_map = None
            self._dragging = False
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            return

        # 左クリック：カテゴリ（属性）選択して更新
        if (not self._dragging) and (event.button() == Qt.LeftButton):
            try:
                self._set_attrs_only(self._drag_fid)
            except Exception as e:
                QMessageBox.critical(self.canvas, "PhotoClicks", f"Update category failed: {e}")
            finally:
                self._drag_fid = None
                self._press_pt_map = None
                self._dragging = False
                self._drag_start_layer_pt = None
                self._drag_start_fid = None
            return

        # ドラッグ：移動のみ（プレビューで既に更新済み）
        if self._dragging:
            try:
                apply_schema(self.target)

                # drop先（layer CRS）
                pt_layer = self._map_to_layer_point(event.mapPoint())

                # subcat index は先に一度だけ取る
                idx_sub = self.target.fields().indexFromName("subcat")

                # ① 動かした点：subcat を空にする（まずクリア）
                if idx_sub >= 0:
                    with EditContext(self.target):
                        self.target.changeAttributeValue(self._drag_fid, idx_sub, None)

                # ② drop先に同座標点があるか確認 → あれば combined 付与（自分＋相手）
                other_fids_at_drop = self._same_coord_fids(self._drag_fid, pt_layer)
                if other_fids_at_drop and idx_sub >= 0:
                    with EditContext(self.target):
                        self.target.changeAttributeValue(self._drag_fid, idx_sub, "combined")
                        for ofid in other_fids_at_drop:
                            self.target.changeAttributeValue(ofid, idx_sub, "combined")

                # ③ 移動前座標：自分以外の同座標点が「1個だけ」なら空にする（2個以上は触らない）
                if self._drag_start_layer_pt is not None and idx_sub >= 0:
                    start_x = float(self._drag_start_layer_pt.x())
                    start_y = float(self._drag_start_layer_pt.y())
                    tol = getattr(self.owner, "COORD_TOL", 1e-7)

                    remain_fids = []
                    for f in self.target.getFeatures():
                        if f.id() == self._drag_fid:
                            continue
                        try:
                            p = f.geometry().asPoint()
                            if abs(p.x() - start_x) <= tol and abs(p.y() - start_y) <= tol:
                                remain_fids.append(f.id())
                        except Exception:
                            pass

                    if len(remain_fids) == 1:
                        with EditContext(self.target):
                            self.target.changeAttributeValue(remain_fids[0], idx_sub, None)

            except Exception:
                pass

            # ★ここは必ず実行
            self.target.triggerRepaint()

            # ★状態クリアは必ず
            self._drag_fid = None
            self._dragging = False
            self._press_pt_map = None
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            return

#DisableCurrentTool
def disable_current_tool(canvas: QgsMapCanvas, prev_tool: Optional[QgsMapTool]) -> None:
    try:
        if prev_tool is not None:
            canvas.setMapTool(prev_tool)
    except Exception:
        pass

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
    conflict: Optional[Tuple[str, str, str, str]] = None,
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
        conflict_tool_attr, conflict_prev_attr, conflict_off_label, conflict_btn_attr = conflict
        if getattr(owner, conflict_tool_attr, None):
            disable_current_tool(canvas, getattr(owner, conflict_prev_attr, None))
            setattr(owner, conflict_tool_attr, None)
            setattr(owner, conflict_prev_attr, None)
            conflict_btn = getattr(owner, conflict_btn_attr)
            _set_btn(conflict_btn, conflict_off_label, False)

    if not target_layer or not target_layer.isValid():
        _set_btn(btn, off_label, False)
        return

    prev, tool = enable_tool(owner, canvas, target_layer, tool_cls)
    setattr(owner, current_tool_attr, tool)
    setattr(owner, prev_tool_attr, prev)
    _set_btn(btn, on_label, True) 
