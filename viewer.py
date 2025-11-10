#viewer.py
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime

from qgis.PyQt.QtGui import QPixmap, QDesktopServices
from qgis.PyQt.QtCore import Qt, QUrl, QStandardPaths
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.core import QgsProject, QgsRectangle
from qgis.utils import iface

from .utils import (
    Row, settings, normalize_header,
    SKEY_ROOT, SKEY_CSV, SKEY_IMG, SKEY_GEOM, SKEY_AUTZOOM,
    resolve_path, get_attr_safe)
from .fields import FN, USER_ATTR_SPECS, MAIN_TO_SUBFIELD

from . import dialogs
from . import layers as lyrmod
from . import symbology as symb
from . import maptools
from . import utils
from . import ui as ui_mod
from . import io as io_mod


class PhotoViewerPlus:
    LAYER_NAME = "PhotoPoints"
    CLICK_LAYER_NAME = "PhotoClicks"
    SKEY_LAST_EXPORT_CLICKS = f"{SKEY_ROOT}last_export_clicks_csv"

    def __init__(self):
        self.images: List[Row] = []
        self.img_dir = Path()
        self.layer = None
        self.click_layer = None
        self.click_layer_id = None
        self._pix_cache: Dict[Tuple[str, int], QPixmap] = {}
        self.current_index = 0
        self.suspend_selection_signal = False
        self.COORD_TOL = 1e-7
        self.auto_zoom = bool(settings.value(SKEY_AUTZOOM, True, type=bool))
        self._idx_by_kp: Dict[str, int] = {}
        self._idx_by_pic: Dict[str, int] = {}
        self._prev_map_tool = None
        self._click_tool = None
        self._delete_tool = None

        self._build_ui()
        
        # --- PyQt5 / PyQt6 互換ヘルパ ---------------------------------
        # PyQt6 では enum が名前空間化される（Qt.WindowModality など）
        try:
            self._WindowModalityEnum = Qt.WindowModality   # PyQt6
        except AttributeError:
            self._WindowModalityEnum = Qt                  # PyQt5 互換

        # QDialog.exec / exec_ の互換呼び出し
        def _exec_dialog(dlg):
            try:
                return dlg.exec()      # PyQt6
            except AttributeError:
                return dlg.exec_()     # PyQt5
        self._exec_dialog = _exec_dialog

        # QStandardPaths StandardLocation の互換参照
        _StdLoc = getattr(QStandardPaths, "StandardLocation", QStandardPaths)
        self._DOCS_LOC = getattr(_StdLoc, "DocumentsLocation", QStandardPaths.DocumentsLocation)
        self._TEMP_LOC = getattr(_StdLoc, "TempLocation", QStandardPaths.TempLocation)

        def _writable_location(loc_enum):
            return QStandardPaths.writableLocation(loc_enum)
        self._writable_location = _writable_location
        # -------------------------------------------------------------

    # UI
    def _build_ui(self):
        self.dock = ui_mod.create_dock(auto_zoom_default=self.auto_zoom)
        self.add_btn = self.dock.add_btn
        self.del_btn = self.dock.del_btn
        self.q_edit  = self.dock.q_edit

        self.dock.prevRequested.connect(self.prev_image)
        self.dock.nextRequested.connect(self.next_image)
        self.dock.configRequested.connect(self.configure_and_load)
        self.dock.gmapsRequested.connect(self._open_gmaps)
        self.dock.addModeToggled.connect(self._toggle_add_mode)
        self.dock.delModeToggled.connect(self._toggle_del_mode)
        self.dock.autoZoomToggled.connect(self._save_autoz)
        self.dock.importClicksRequested.connect(self._import_clicks_csv)
        self.dock.exportClicksRequested.connect(self._export_clicks_csv)
        self.dock.jumpRequested.connect(self._jump_text)
        self.dock.imageDoubleClicked.connect(lambda side: self._on_image_dblclick(None))

    def _jump_text(self, text: str):
        if hasattr(self, "q_edit"):
            self.q_edit.setText(text or "")
        key = (text or "").strip().lower()
        if not key:
            return
        i = None
        if key in self._idx_by_kp:
            i = self._idx_by_kp[key]
        elif key in self._idx_by_pic:
            i = self._idx_by_pic[key]
        if i is None:
            QMessageBox.information(iface.mainWindow(), "Jump", f"Not found: {key}")
            return
        self.show_image(i)

    def _pick_paths(self) -> Tuple[str, str]:
        last_csv = settings.value(SKEY_CSV, '', type=str) or ''
        last_img = settings.value(SKEY_IMG, '', type=str) or ''
        csv_file, _ = QFileDialog.getOpenFileName(
            iface.mainWindow(),
            "Select CSV(kp,lat_kp,lon_kp,street,pic_front,lat_front,lon_front,course_front,pic_back,lat_back,lon_back,course_back)",
            last_csv, "CSV (*.csv)"
        )
        if not csv_file:
            raise Exception("No CSV Selected")
        img_dir = QFileDialog.getExistingDirectory(iface.mainWindow(), "Select image folder", last_img)
        if not img_dir:
            raise Exception("No image folder selected")
        settings.setValue(SKEY_CSV, csv_file); settings.setValue(SKEY_IMG, img_dir)
        return csv_file, img_dir

    def _ensure_point_layer(self):
        if self.layer and self.layer.isValid():
            return self.layer
        self.layer = lyrmod.ensure_point_layer(self.LAYER_NAME)
        lyrmod.ensure_sel_fields(self.layer)
        symb.apply_plane_symbology(self.layer)
        self._hook_layer(self.layer)
        return self.layer
    
    # -------------- 表示制御 --------------
    def _set_pixmap(self, side: str, key: Tuple[str, int], path: Path):
        if not path.is_file():
            self.dock.set_message(side, f"Image not found:\n{path}"); return
        pix = QPixmap(str(path))
        if pix.isNull():
            self.dock.set_message(side, f"Failed to open image:\n{path}"); return
        self._pix_cache[key] = pix
        self.dock.set_pixmap(side, pix)

    def _update_name_labels(self, row: Row, disp_front: Optional[str] = None, disp_back: Optional[str] = None):
        p_front = resolve_path(self.img_dir, (disp_front if disp_front is not None else row.front) or "")
        p_back  = resolve_path(self.img_dir, (disp_back  if disp_back  is not None else row.back)  or "")
        
        def _kp_for_pic(pic: Optional[str]) -> Optional[str]:
            key = (pic or "").strip().lower()
            if not key:
                return None
            i = self._idx_by_pic.get(key)
            if i is None:
                return None
            try:
                return self.images[i].kp
            except Exception:
                return None

        kp_front = _kp_for_pic(disp_front if disp_front is not None else row.front)
        kp_back  = _kp_for_pic(disp_back  if disp_back  is not None else row.back)
        
        try:
            fn_front = p_front.name if p_front.name else "—"
            fn_back  = p_back.name  if p_back.name  else "—"

            kp_text_front = f"(KP:{kp_front})" if kp_front else "(KP: —)"
            kp_text_back  = f"(KP:{kp_back})"  if kp_back  else "(KP: —)"

            # 上段の見出し行
            self.dock.set_inline_names(
                front_text=f"{fn_front}  {kp_text_front}",
                front_tooltip=str(p_front) if p_front else "",
                back_text=f"{fn_back}  {kp_text_back}",
                back_tooltip=str(p_back) if p_back else "",
            )

        except Exception:
            pass

    def _select_features(self, feats):
        if not (self.layer and feats):
            return

        ids = [f.id() for f in feats if f is not None]
        if not ids:
            return

        # QGISの選択はしない。色はsymbologyのフィールドで制御
        if self.auto_zoom:
            try:
                iface.mapCanvas().zoomToFeatureIds(self.layer, ids)
                iface.mapCanvas().zoomScale(500)
                iface.mapCanvas().refresh()
            except Exception:
                pass

    # -------------- 画像／レコード操作 --------------
    def show_image(self, idx: int):
        if not self.images:
            self.dock.set_message("front", "CSV not loaded. Configure it via Select Data.")
            self.dock.set_message("back",  "CSV not loaded. Configure it via Select Data.")
            return

        self.current_index = idx % len(self.images)
        row = self.images[self.current_index]

        if (row.lat_kp is None) or (row.lon_kp is None):
            self.dock.set_message("front", "No KP")
            self.dock.set_message("back",  "No KP")
            return

        prev_row = next_row = None
        if hasattr(self, "_kp_order") and self._kp_order:
            try:
                pos = self._kp_order.index(self.current_index)
                if pos > 0:
                    prev_row = self.images[self._kp_order[pos - 1]]
                if pos < len(self._kp_order) - 1:
                    next_row = self.images[self._kp_order[pos + 1]]
            except Exception:
                pass

        def _same_street(r1, r2):
            return (
                r1 is not None and
                r2 is not None and
                (getattr(r1, "street", "") or "") == (getattr(r2, "street", "") or "")
            )

        disp_front = None
        disp_back = None

        # front → 同じstreetの1つ前からだけ借りる
        if _same_street(row, prev_row):
            disp_front = prev_row.front or prev_row.back

        # back → 同じstreetの1つ後からだけ借りる
        if _same_street(row, next_row):
            disp_back = next_row.back or next_row.front

        # 両方とも取れなかったら非表示
        if not disp_front and not disp_back:
            self.dock.set_message("front", "No image")
            self.dock.set_message("back",  "No image")
            self.dock.set_inline_names("—", "", "—", "")
            return

        # ここから実表示
        if disp_front:
            self._set_pixmap("front", ("front", self.current_index), resolve_path(self.img_dir, disp_front))
        else:
            self.dock.set_message("front", "No image")
        if disp_back:
            self._set_pixmap("back", ("back", self.current_index), resolve_path(self.img_dir, disp_back))
        else:
            self.dock.set_message("back",  "No image")

        self._update_name_labels(row, disp_front, disp_back)

        # 地図のフィーチャのハイライト
        feats = []
        ff = lyrmod.find_feature_by_pic_or_coord(self.layer,
            disp_front,
            row.lat_front, row.lon_front,
            expected_side="front",
            tol=self.COORD_TOL,
        ) if disp_front else None
        if ff:
            feats.append(ff)
        fb = lyrmod.find_feature_by_pic_or_coord(self.layer,
            disp_back,
            row.lat_back, row.lon_back,
            expected_side="back",
            tol=self.COORD_TOL,
        ) if disp_back else None
        if fb:
            feats.append(fb)

        lyrmod.apply_front_back_selected(self.layer, ff, fb)
        if feats:
            self._select_features(feats)

        lyrmod.select_kp(self.layer, row.kp)

    def next_image(self):
        self.show_image(self.current_index + 1)

    def prev_image(self):
        self.show_image(self.current_index - 1)

    def force_disable_map_tools(self):
        """プラグイン終了時などに、Add/Delete の独自ツールを確実に解除する"""
        canvas = iface.mapCanvas()
        for attr in ("_click_tool", "_delete_tool"):
            tool = getattr(self, attr, None)
            if tool:
                maptools.disable_current_tool(canvas, getattr(self, "_prev_map_tool", None))
                setattr(self, attr, None)

    # -------------- コンフィグ／ロード --------------
    def configure_and_load(self):
        try:
            csv_file, img_dir_sel = self._pick_paths()
            # 進捗UIはここでだけ扱い、実体は io で処理
            from qgis.PyQt.QtWidgets import QProgressDialog
            prog = QProgressDialog("Loading CSV", "Cancel", 0, 0, iface.mainWindow())
            prog.setWindowModality(getattr(self._WindowModalityEnum, "ApplicationModal", Qt.ApplicationModal))
            prog.setMinimumDuration(400)
            def _tick(i):
                prog.setLabelText(f"Loading{i:,} rows…"); prog.setValue(0)
                if prog.wasCanceled():
                    raise Exception("Operation was canceled by the user")
            try:
                rows = io_mod.load_images_csv(csv_file, on_progress=_tick)
            finally:
                try:
                    prog.close()
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "PhotoViewer Configuration Error", str(e))
            return

        self.images = rows
        self.img_dir = Path(img_dir_sel)
        self._rebuild_index()

        lyr = self._ensure_point_layer()
        self._plot_all_points(lyr)
        self._zoom_to_first_row()
        self.show_image(0)

    def _zoom_to_first_row(self):
        if not self.images:
            return

        target_lon = None
        target_lat = None
        for r in self.images:
            lon = r.lon_front if r.lon_front is not None else r.lon_back
            lat = r.lat_front if r.lat_front is not None else r.lat_back
            if lon is not None and lat is not None:
                target_lon = lon
                target_lat = lat
                break

        if target_lon is None or target_lat is None:
            return

        pad = 0.01

        rect = QgsRectangle(
            target_lon - pad, target_lat - pad,
            target_lon + pad, target_lat + pad,
        )

        canvas = iface.mapCanvas()
        canvas.setExtent(rect)
        canvas.refresh()

    def _rebuild_index(self):
        self._idx_by_kp.clear()
        self._idx_by_pic.clear()
        kp_order: List[Tuple[str, Tuple[int, float, str], int]] = []

        def _kp_numeric(kp_text: str) -> Tuple[int, float, str]:
            s = re.sub(r'[^0-9.\-]+', '', kp_text or '')
            try:
                return (0, float(s), '')
            except Exception:
                return (1, 0.0, kp_text or '')

        for i, r in enumerate(self.images):
            if r.kp:
                key = str(r.kp).strip().lower()
                if key not in self._idx_by_kp:
                    self._idx_by_kp[key] = i

            for nm in (r.front, r.back):
                k = (nm or "").strip().lower()
                if k:
                    self._idx_by_pic[k] = i

            street_key = (getattr(r, "street", "") or "").strip().lower()
            ord_key = _kp_numeric(str(r.kp))
            kp_order.append((street_key, ord_key, i))

        kp_order.sort(key=lambda t: (t[0], t[1]))
        self._kp_order = [i for _, _, i in kp_order]

    def _plot_all_points(self, layer):
        if not self.images:
            QMessageBox.information(iface.mainWindow(), "PhotoViewer", "CSV not loaded")
            return

        def _info(n):
            QMessageBox.information(iface.mainWindow(), "PhotoViewer", f"Plot completed: add {n} points.")

        lyrmod.plot_all_points(layer, self.images, info_cb=_info)
        ext = layer.extent()
        if ext and not ext.isEmpty():
            iface.mapCanvas().setExtent(ext); iface.mapCanvas().refresh()

    # -------------- 選択連動 --------------
    def _hook_layer(self, layer_obj):
        try:
            layer_obj.selectionChanged.disconnect(self._on_layer_selection_changed)
        except Exception:
            pass
        layer_obj.selectionChanged.connect(self._on_layer_selection_changed)
        prj = QgsProject.instance()
        try:
            prj.layerWillBeRemoved.disconnect(self._on_layer_will_be_removed)
        except Exception:
            pass
        prj.layerWillBeRemoved.connect(self._on_layer_will_be_removed)

    def _on_layer_will_be_removed(self, layer_id: str):
        if self.layer and self.layer.id() == layer_id:
            self.layer = None
        if getattr(self, "click_layer", None) and self.click_layer.id() == layer_id:
            self.click_layer = None

    def _on_layer_selection_changed(self, *args):
        if self.suspend_selection_signal or not self.layer or not self.images:
            return
        sel = list(self.layer.selectedFeatures())
        if not sel:
            return
        f = sel[0]
        try:
            kp_val = (get_attr_safe(f, "kp", "") or "").strip().lower()
            if kp_val and kp_val in self._idx_by_kp:
                self.show_image(self._idx_by_kp[kp_val]); return
        except Exception:
            pass
        for key in (FN.JPG, "pic_front", "pic_back"):
            try:
                nm = (f[key] or "").strip().lower()
                if nm and nm in self._idx_by_pic:
                    self.show_image(self._idx_by_pic[nm]); return
            except Exception:
                pass
        try:
            pt = f.geometry().asPoint()
            for i, r in enumerate(self.images):
                if (r.lon_front is not None and r.lat_front is not None and
                    abs(r.lon_front - pt.x()) <= self.COORD_TOL and abs(r.lat_front - pt.y()) <= self.COORD_TOL):
                    self.show_image(i); return
                if (r.lon_back is not None and r.lat_back is not None and
                    abs(r.lon_back - pt.x()) <= self.COORD_TOL and abs(r.lat_back - pt.y()) <= self.COORD_TOL):
                    self.show_image(i); return
        except Exception:
            pass

    def _prompt_attributes(self) -> Optional[List[Dict[str, str]]]:
        last: Dict[str, str] = {}
        dlg = dialogs.AttrDialog(iface.mainWindow(), USER_ATTR_SPECS, last)
        res = self._exec_dialog(dlg)
        if res != dlg.Accepted:
            return None

        selected = dlg.values()
        if not selected:
            QMessageBox.information(
                iface.mainWindow(),
                "Select Attributes",
                "No category selected."
            )
            return None

        selected_lc = { (k or "").strip().lower(): (v or "") for k, v in selected.items() }
        chosen_main = [k for k, v in selected_lc.items() if v]

        results: List[Dict[str, str]] = []

        # '=n' を展開する: 'stop=2,yield' → ['stop','stop','yield']
        def _parse_sub_vals(val: str) -> List[str]:
            if not val:
                return []
            out: List[str] = []
            for tok in [t.strip().lower() for t in val.split(",") if t.strip()]:
                m = re.match(r"^(.*?)(?:\s*=\s*(\d+))?$", tok)
                if not m:
                    continue
                label = (m.group(1) or "").strip()
                if not label:
                    continue
                n = int(m.group(2)) if m and m.group(2) else 1
                n = max(1, min(n, 999))
                out.extend([label] * n)
            return out

        # 総アイテム数で multi 判定
        total_items = sum(len(_parse_sub_vals(selected_lc.get(main, ""))) for main in chosen_main)
        will_be_multi = total_items >= 2

        if chosen_main:
            for main in chosen_main:
                sub_vals = _parse_sub_vals(selected_lc.get(main, ""))  # '=n' 展開済み

                if sub_vals:
                    # sub ごとに 1 レコードずつ作成（stop=2 → 2レコード）
                    for sub in sub_vals:
                        d: Dict[str, str] = {FN.CATEGORY: main}  # main は小文字のまま
                        for k, v in selected.items():
                            if not v:
                                continue
                            key = normalize_header(k)
                            low = key.lower()
                            if low in ("category", "categories", "カテゴリ", "カテゴリー"):
                                continue
                            if k.lower() == main:
                                key = MAIN_TO_SUBFIELD.get(main, key)
                                d[key] = sub  # 1件ずつ sub を入れる
                                continue
                            if key in ("lat", "lon", "jpg"):
                                key = f"user_{key}"
                            d[key] = v
                        if will_be_multi:
                            d["subcat"] = "combined"
                        results.append(d)
                else:
                    d: Dict[str, str] = {FN.CATEGORY: main}
                    for k, v in selected.items():
                        if not v:
                            continue
                        key = normalize_header(k)
                        if key in ("lat", "lon", "jpg"):
                            key = f"user_{key}"
                        d[key] = v
                    if will_be_multi:
                        d["subcat"] = "combined"
                    results.append(d)
        else:
            d: Dict[str, str] = {}
            for k, v in selected.items():
                if not v:
                    continue
                key = normalize_header(k)
                if key in ("lat", "lon", "jpg"):
                    key = f"user_{key}"
                d[key] = v
            if d:
                if "," in (selected.get("category", "") or ""):
                    d["subcat"] = "combined"
                results.append(d)

        return results or None

    # -------------- クリック追加（PhotoClicks） --------------
    def _toggle_add_mode(self):
        try:
            lyr = lyrmod.ensure_click_layer(self.CLICK_LAYER_NAME)
            self.click_layer = lyr
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "PhotoClicks", f"Failed to create click layer\n{e}")
            return
        
        maptools.toggle_tool_mode(
            self, iface.mapCanvas(), lyr,
            "_click_tool", "_prev_map_tool",
            maptools.AddPointTool,
            "● Add Click mode (ON)", "● Add Click mode", self.add_btn,
            conflict=("_delete_tool", "_prev_map_tool", "✖ Delete Click mode", "del_btn", iface)
        )

    def _toggle_del_mode(self):
        try:
            lyr = lyrmod.ensure_click_layer(self.CLICK_LAYER_NAME)
            self.click_layer = lyr
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "PhotoClicks", f"Failed to create click layer\n{e}")
            return
        
        maptools.toggle_tool_mode(
            self, iface.mapCanvas(), lyr,
            "_delete_tool", "_prev_map_tool",
            maptools.DeletePointTool,
            "✖ Delete Click mode (ON)", "✖ Delete Click mode", self.del_btn,
            conflict=("_click_tool", "_prev_map_tool", "● Add Click mode", "add_btn", iface)
        )

    def _export_clicks_csv(self):
        try:
            lyr = lyrmod.ensure_click_layer(self.CLICK_LAYER_NAME)
            self.click_layer = lyr
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "Export CSV(Clicks)", f"Failed to create click layer\n{e}")
            return
        if lyr is None:
            QMessageBox.warning(iface.mainWindow(), "Export CSV(Clicks)", "No target PhotoClicks layer to export")
            return
        last_path = settings.value(self.SKEY_LAST_EXPORT_CLICKS, "", type=str) if hasattr(self, "SKEY_LAST_EXPORT_CLICKS") else ""
        if not last_path:
            base_dir = self._writable_location(self._DOCS_LOC) or self._writable_location(self._TEMP_LOC)
            last_path = str(Path(base_dir) / "photo_clicks.csv")
        out_csv, _ = QFileDialog.getSaveFileName(
            iface.mainWindow(),
            "Save PhotoClicks as CSV",
            last_path,
            "CSV (*.csv)"
        )
        if not out_csv:
            return

        cur_kp = ""
        if self.images and 0 <= self.current_index < len(self.images):
            cur_kp = str(self.images[self.current_index].kp or "")
        meta = {
            "clicks_csv": str(out_csv),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "kp": cur_kp,
        }

        sel_only = False
        if lyr.selectedFeatureCount() > 0:
            reply = QMessageBox.question(
                iface.mainWindow(),
                "Export CSV (Clicks)",
                "Export only selected features?\n Choose 'No' to export all.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            sel_only = (reply == QMessageBox.Yes)

        try:
            out_csv, meta_path = io_mod.export_clicks_csv(lyr, out_csv, sel_only, meta)
            if hasattr(self, "SKEY_LAST_EXPORT_CLICKS"):
                settings.setValue(self.SKEY_LAST_EXPORT_CLICKS, out_csv)
            if meta_path:
                settings.setValue(f"{SKEY_ROOT}last_clicks_meta", meta_path)
            QMessageBox.information(iface.mainWindow(), "Export CSV (Clicks)", f"Saved:\n{out_csv}")
        except Exception as e:
            QMessageBox.critical(
                iface.mainWindow(),
                "Save CSV Error (Clicks)",
                f"Failed to save CSV\n{e}\n\n"
                "Try saving to a writable folder (e.g., Documents) or closing apps that may have the file open (e.g., Excel)."
            )

    def _import_clicks_csv(self):
        try:
            lyr = lyrmod.ensure_click_layer(self.CLICK_LAYER_NAME)
            self.click_layer = lyr
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "PhotoClicks", f"Failed to create click layer\n{e}")
            return

        # 以降は元の処理（ファイル選択〜CSV読み込み）
        last_path = settings.value(self.SKEY_LAST_EXPORT_CLICKS, "", type=str) if hasattr(self, "SKEY_LAST_EXPORT_CLICKS") else ""
        csv_file, _ = QFileDialog.getOpenFileName(
            iface.mainWindow(),
            "Select PhotoClicks CSV (lat,lon,jpg,category)",
            last_path or "",
            "CSV (*.csv);;All Files (*)"
        )
        if not csv_file:
            return

        from qgis.PyQt.QtWidgets import QProgressDialog
        prog = QProgressDialog("Loading Clicks CSV…", "Cancel", 0, 0, iface.mainWindow())
        prog.setWindowModality(getattr(self._WindowModalityEnum, "ApplicationModal", Qt.ApplicationModal))
        prog.setMinimumDuration(400)
        def _tick(i):
            prog.setLabelText(f"Loading {i:,} rows …"); prog.setValue(0)
            if prog.wasCanceled():
                raise Exception("Operation was canceled by the user")
        try:
            added, skipped, target_kp = io_mod.import_clicks_csv(
                lyr, csv_file, dst_crs=lyr.crs(), clear=True, on_progress=_tick
            )
            # ズーム＆メッセージ
            lyr.triggerRepaint()
            ext = lyr.extent()
            if ext and not ext.isEmpty():
                iface.mapCanvas().setExtent(ext); iface.mapCanvas().refresh()
            idx = None
            if target_kp:
                idx = self._idx_by_kp.get(target_kp.strip().lower())
            msg = f"Import Completed: added {added} / skipped {skipped}."
            if target_kp:
                if idx is not None:
                    msg += f"\n Jumping to the last worked KP({target_kp})"
                else:
                    msg += f"\n Last worked KP ({target_kp}) was not found"
            QMessageBox.information(iface.mainWindow(), "Import Clicks CSV", msg)
            if idx is not None:
                self.show_image(idx)
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "Import Clicks CSV", f"Failed to import\n{e}")
        finally:
            try:
                prog.close()
            except Exception:
                pass

    # googlemap link
    def _open_gmaps(self):
        if not self.images:
            return

        row = self.images[self.current_index]
        lat, lon = row.lat_kp, row.lon_kp

        if lat is None or lon is None:
            QMessageBox.information(
                iface.mainWindow(),
                "Google Street View", "This record has no valid coordinates")
            return
        heading = (
            row.course_front 
            if row.course_front is not None
            else (row.course_back if row.course_back is not None else 0.0)
)

        try:
            url = utils.make_streetview_url(lat, lon, heading)
            QDesktopServices.openUrl(QUrl.fromUserInput(url))
        except Exception:
            fallback = utils.make_gmaps_search_url(lat, lon)
            QDesktopServices.openUrl(QUrl.fromUserInput(fallback))

# -------------- 補助 --------------
    def _on_image_dblclick(self, ev):
        if not self.images:
            return
        row = self.images[self.current_index]
        disp_front, disp_back = row.front, row.back
        prev_row = next_row = None
        if hasattr(self, "_kp_order") and self._kp_order:
            try:
                pos = self._kp_order.index(self.current_index)
                if pos > 0:
                    prev_row = self.images[self._kp_order[pos - 1]]
                if pos < len(self._kp_order) - 1:
                    next_row = self.images[self._kp_order[pos + 1]]
            except Exception:
                pass
        if (row.lat_kp is not None) and (row.lon_kp is not None):
            if prev_row and getattr(prev_row, "street", "") == getattr(row, "street", ""):
                disp_front = prev_row.front or prev_row.back or disp_front
            if next_row and getattr(next_row, "street", "") == getattr(row, "street", ""):
                disp_back = next_row.back  or next_row.front or disp_back

        for p in [resolve_path(self.img_dir, disp_front or ""), resolve_path(self.img_dir, disp_back or "")]:
            try:
                if p and p.is_file():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
            except Exception:
                pass

    def _save_autoz(self, checked: bool):
        self.auto_zoom = bool(checked)
        settings.setValue(SKEY_AUTZOOM, self.auto_zoom)