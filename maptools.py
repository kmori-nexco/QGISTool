#maptool.py
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
from . import layers as lyrmod


def _qt_enum(container, scoped_name: str, legacy_name: str = None, default=None):
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


_CURSOR_CROSS = _qt_enum(Qt, "CursorShape.CrossCursor", "CrossCursor")
_MOUSE_LEFT = _qt_enum(Qt, "MouseButton.LeftButton", "LeftButton")
_MOUSE_RIGHT = _qt_enum(Qt, "MouseButton.RightButton", "RightButton")
_ASPECT_KEEP = _qt_enum(Qt, "AspectRatioMode.KeepAspectRatio", "KeepAspectRatio")
_TRANSFORM_SMOOTH = _qt_enum(Qt, "TransformationMode.SmoothTransformation", "SmoothTransformation")


class AddPointTool(QgsMapTool):
    def __init__(self, owner, canvas, target_layer):
        super().__init__(canvas)
        self.owner = owner
        self.canvas = canvas
        self.target = target_layer

        if QCursor:
            self.setCursor(QCursor(_CURSOR_CROSS))
        else:
            self.setCursor(_CURSOR_CROSS)

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

        try:
            map_crs = self.canvas.mapSettings().destinationCrs()
            layer_crs = self.target.crs()
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
                if will_be_combined and not (extra_attrs.get("subcat") or extra_attrs.get("subCategory")):
                    extra_attrs["subcat"] = "combined"

                names_now = set(self.target.fields().names())
                need = [k for k in extra_attrs.keys() if k not in names_now]
                if need:
                    with EditContext(self.target):
                        from qgis.PyQt.QtCore import QVariant
                        from qgis.core import QgsField
                        self.target.dataProvider().addAttributes([QgsField(k, QVariant.String) for k in need])
                        self.target.updateFields()

                f = QgsFeature(self.target.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt_layer.x(), pt_layer.y())))

                ilat = self.target.fields().indexFromName(FN.LAT)
                ilon = self.target.fields().indexFromName(FN.LON)
                ijpg = self.target.fields().indexFromName(FN.JPG)
                if ilat >= 0:
                    f.setAttribute(ilat, float(pt_layer.y()))
                if ilon >= 0:
                    f.setAttribute(ilon, float(pt_layer.x()))
                if ijpg >= 0:
                    f.setAttribute(ijpg, jpg_val)

                for k, v in extra_attrs.items():
                    idx = self.target.fields().indexFromName(k)
                    if idx >= 0:
                        f.setAttribute(idx, v)

                raw_category = extra_attrs.get(FN.CATEGORY) or extra_attrs.get("category") or ""
                category_norm = normalize_category(raw_category)
                clear_unrelated_category_attrs(self.target, f, category_norm)

                if not self.target.addFeatures([f]):
                    raise Exception("addFeatures failed.")

            self.target.commitChanges()
            lyrmod.update_same_point_counts(self.target)
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

        self._press_pt_map = None
        self._drag_fid = None
        self._drag_fids = None
        self._dragging = False
        self.last_preview_t = 0.0
        self._drag_start_layer_pt = None
        self._drag_start_fid = None

        cursor_path = Path(__file__).parent / "icons" / "editmode_cursor.png"
        try:
            if QPixmap and cursor_path.exists():
                pm = QPixmap(str(cursor_path))
                if not pm.isNull():
                    pm = pm.scaled(24, 24, _ASPECT_KEEP, _TRANSFORM_SMOOTH)
                    if QCursor:
                        self.setCursor(QCursor(pm, 0, 0))
                        return
        except Exception:
            pass

        if QCursor:
            self.setCursor(QCursor(_CURSOR_CROSS))
        else:
            self.setCursor(_CURSOR_CROSS)

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

        if not self._dragging:
            dx = event.mapPoint().x() - self._press_pt_map.x()
            dy = event.mapPoint().y() - self._press_pt_map.y()
            if (dx * dx + dy * dy) > (self.canvas.mapSettings().mapUnitsPerPixel() * 3) ** 2:
                self._dragging = True
            else:
                return

        now = time.time()
        if (now - self.last_preview_t) < 0.05:
            return
        self.last_preview_t = now

        try:
            pt_layer = self._map_to_layer_point(event.mapPoint())
            g = QgsGeometry.fromPointXY(QgsPointXY(pt_layer.x(), pt_layer.y()))

            fids = self._drag_fids or ([self._drag_fid] if self._drag_fid is not None else [])
            if not fids:
                return

            ilat = self.target.fields().indexFromName(FN.LAT)
            ilon = self.target.fields().indexFromName(FN.LON)

            with EditContext(self.target):
                for fid in fids:
                    try:
                        self.target.changeGeometry(fid, g)
                    except Exception:
                        self.target.dataProvider().changeGeometryValues({fid: g})

                    if ilat >= 0:
                        self.target.changeAttributeValue(fid, ilat, float(pt_layer.y()))
                    if ilon >= 0:
                        self.target.changeAttributeValue(fid, ilon, float(pt_layer.x()))

            self.target.triggerRepaint()
        except Exception:
            pass

    def _set_attrs_only(self, fid: int) -> None:
        apply_schema(self.target)

        extra = self._prompt_single_attrs_for_edit()
        if not extra:
            return

        with EditContext(self.target):
            for k, v in extra.items():
                idx = self.target.fields().indexFromName(k)
                if idx >= 0:
                    self.target.changeAttributeValue(fid, idx, v)

        raw_category = (extra.get(FN.CATEGORY) if isinstance(extra, dict) else "") or ""
        category_norm = normalize_category(raw_category)

        f = next(self.target.getFeatures(f"id={fid}"), None)
        if f is not None:
            clear_unrelated_category_attrs(self.target, f, category_norm)
            with EditContext(self.target):
                for cand in (FN.TRAFFIC_SIGN, FN.POLE, FN.FIREHYDRANT, FN.UNKNOWN):
                    idxc = self.target.fields().indexFromName(cand)
                    if idxc >= 0:
                        self.target.changeAttributeValue(fid, idxc, f[cand])

        self.target.triggerRepaint()

    def _delete_feature_with_confirm(self, feat: QgsFeature):
        try:
            jpg_val = str(feat[FN.JPG] or "")
        except Exception:
            jpg_val = ""

        _StdBtn = getattr(QMessageBox, "StandardButton", QMessageBox)
        _YES = getattr(_StdBtn, "Yes", QMessageBox.Yes)
        _NO = getattr(_StdBtn, "No", QMessageBox.No)

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
        lyrmod.update_same_point_counts(self.target)
        self.target.triggerRepaint()

    def _prompt_single_attrs_for_edit(self) -> Optional[dict]:
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

    def canvasPressEvent(self, event):
        if not self.target or not self.target.isValid():
            return

        if event.button() == _MOUSE_RIGHT:
            self._press_pt_map = None
            self._drag_fid = None
            self._drag_fids = None
            self._dragging = False
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            return

        self._press_pt_map = event.mapPoint()
        self._dragging = False

        feat, _ = self._nearest_feature(self._press_pt_map)
        self._drag_fid = feat.id() if feat else None

        self._drag_start_layer_pt = None
        self._drag_start_fid = self._drag_fid
        self._drag_fids = None

        if feat is not None:
            try:
                p = feat.geometry().asPoint()
                pt0 = QgsPointXY(float(p.x()), float(p.y()))
                self._drag_start_layer_pt = pt0
                same = self._same_coord_fids(self._drag_fid, pt0)
                self._drag_fids = [self._drag_fid] + same
            except Exception:
                self._drag_fids = [self._drag_fid] if self._drag_fid is not None else None

    def canvasReleaseEvent(self, event):
        if not self.target or not self.target.isValid():
            return

        if (not self._dragging) and (event.button() == _MOUSE_RIGHT):
            feat, _ = self._nearest_feature(event.mapPoint())
            if feat:
                try:
                    self._delete_feature_with_confirm(feat)
                except Exception as e:
                    QMessageBox.critical(self.canvas, "PhotoClicks", f"Delete failed: {e}")

            self._drag_fid = None
            self._drag_fids = None
            self._press_pt_map = None
            self._dragging = False
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            return

        if self._drag_fid is None:
            self._press_pt_map = None
            self._dragging = False
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            self._drag_fids = None
            return

        if (not self._dragging) and (event.button() == _MOUSE_LEFT):
            try:
                self._set_attrs_only(self._drag_fid)
            except Exception as e:
                QMessageBox.critical(self.canvas, "PhotoClicks", f"Update category failed: {e}")
            finally:
                self._drag_fid = None
                self._drag_fids = None
                self._press_pt_map = None
                self._dragging = False
                self._drag_start_layer_pt = None
                self._drag_start_fid = None
            return

        if self._dragging:
            try:
                apply_schema(self.target)
            except Exception:
                pass

            lyrmod.update_same_point_counts(self.target)
            self.target.triggerRepaint()

            self._drag_fid = None
            self._drag_fids = None
            self._dragging = False
            self._press_pt_map = None
            self._drag_start_layer_pt = None
            self._drag_start_fid = None
            return


def disable_current_tool(canvas: QgsMapCanvas, prev_tool: Optional[QgsMapTool]) -> None:
    try:
        if prev_tool is not None:
            canvas.setMapTool(prev_tool)
    except Exception:
        pass


def enable_tool(owner, canvas: QgsMapCanvas, target_layer, tool_cls: Type[QgsMapTool]
                ) -> Tuple[Optional[QgsMapTool], QgsMapTool]:
    if not target_layer or not target_layer.isValid():
        raise ValueError("enable_tool: target_layer is invalid")
    prev = canvas.mapTool()
    tool = tool_cls(owner, canvas, target_layer)
    canvas.setMapTool(tool)
    return prev, tool


def _set_btn(btn, text, checked):
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
        disable_current_tool(canvas, getattr(owner, prev_tool_attr, None))
        setattr(owner, current_tool_attr, None)
        setattr(owner, prev_tool_attr, None)
        _set_btn(btn, off_label, False)
        return

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
