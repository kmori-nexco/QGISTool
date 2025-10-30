import re, csv
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from qgis.PyQt.QtGui import QPixmap, QColor
from qgis.PyQt.QtCore import Qt, QStandardPaths, QVariant

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsMapLayer
)
from qgis.utils import iface

from .utils import (
    Row, EditContext, settings,
    SKEY_ROOT, SKEY_CSV, SKEY_IMG, SKEY_GEOM, SKEY_AUTZOOM,
    open_with_fallback, parse_float, header_map, normalize_header,
)
from . import dialogs
from . import layers as lyrmod
from . import maptools
from .viewer_ui import PhotoViewerUI
from .viewer_qgis import QgisService


class PhotoViewerController:
    """元のPhotoViewerPlusの“中身”をここへ移植。UIとQGIS依存は別モジュールへ。"""

    LAYER_NAME = "PhotoPoints"
    CLICK_LAYER_NAME = "PhotoClicks"
    SKEY_LAST_EXPORT_CLICKS = f"{SKEY_ROOT}last_export_clicks_csv"

    def __init__(self, ui: PhotoViewerUI, qgis: Optional[QgisService] = None):
        self.ui = ui
        self.qgis = qgis or QgisService()

        self.images: List[Row] = []
        self.img_dir = Path()
        self.layer: Optional[QgsVectorLayer] = None
        self.click_layer: Optional[QgsVectorLayer] = None
        self._pix_cache: Dict[Tuple[str, int], QPixmap] = {}
        self.current_index = 0
        self.suspend_selection_signal = False
        self.COORD_TOL = 1e-7
        self.auto_zoom = bool(settings.value(SKEY_AUTZOOM, True, type=bool))
        self._idx_by_kp: Dict[str, int] = {}
        self._idx_by_pic: Dict[str, int] = {}
        self.USER_ATTRS: List[Tuple[str, Optional[List[str]]]] = [
            ("Traffic Sign", ["Stop", "Do not Enter", "Other"]),
            ("Pole", ["Utility", "Light"]),
            ("drain inlet", ["drain inlet"]),
        ]

        # 初期設定
        self.ui.set_autozoom_checked(self.auto_zoom)
        self.qgis.set_canvas_selection_color(QColor(0, 0, 0, 255))

        # UIイベント配線
        self._connect_ui_signals()

        # Dockジオメトリ復元
        try:
            geom = settings.value(SKEY_GEOM, None)
            if geom:
                self.ui.dockwidget().restoreGeometry(geom)
        except Exception:
            pass

        # プロジェクトのレイヤ監視
        prj = self.qgis.project()
        try:
            prj.layerWillBeRemoved.disconnect(self._on_layer_will_be_removed)
        except Exception:
            pass
        prj.layerWillBeRemoved.connect(self._on_layer_will_be_removed)

        # Dock破棄時
        self.ui.dockwidget().destroyed.connect(self._on_dock_destroyed)

    # ------------- UI signal wiring -------------
    def _connect_ui_signals(self):
        self.ui.sigPrev.connect(self.prev_image)
        self.ui.sigNext.connect(self.next_image)
        self.ui.sigConfigure.connect(self.configure_and_load)
        self.ui.sigToggleAdd.connect(self._toggle_add_mode)
        self.ui.sigToggleDel.connect(self._toggle_del_mode)
        self.ui.sigJump.connect(self._jump)
        self.ui.sigAutoZoomToggled.connect(self._save_autoz)
        self.ui.sigExportClicks.connect(self._export_clicks_csv)
        self.ui.sigImageDblClicked.connect(self._on_image_dblclick)
        self.ui.sigResized.connect(self._on_resized)

    # ------------- CSV 読み込み -------------
    def _pick_paths(self) -> Tuple[str, str]:
        last_csv = settings.value(SKEY_CSV, '', type=str) or ''
        last_img = settings.value(SKEY_IMG, '', type=str) or ''
        csv_file, img_dir = self.qgis.pick_paths(last_csv, last_img)
        settings.setValue(SKEY_CSV, csv_file)
        settings.setValue(SKEY_IMG, img_dir)
        return csv_file, img_dir

    def _load_csv(self, csv_file: str) -> List[Row]:
        rows: List[Row] = []
        f, enc = open_with_fallback(csv_file)
        with f:
            sample = f.read(8192); f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
            except Exception:
                import csv as _csv
                if "\t" in sample:
                    dialect = _csv.excel_tab
                elif ";" in sample and sample.count(";") > sample.count(","):
                    dialect = _csv.excel; dialect.delimiter = ";"
                else:
                    dialect = _csv.excel
            rdr = csv.DictReader(f, dialect=dialect)
            headers = header_map(rdr.fieldnames or [])

            required = {"kp","pic_front","lat_front","lon_front","pic_back","lat_back","lon_back"}
            missing = sorted(required - set(headers.keys()))
            if missing:
                detected = ", ".join([normalize_header(h) for h in (rdr.fieldnames or [])])
                raise Exception(
                    "CSVヘッダが不足しています。\n"
                    f"不足: {', '.join(missing)}\n"
                    "必須: kp, pic_front, lat_front, lon_front, pic_back, lat_back, lon_back\n"
                    "任意: course_front, course_back, lat_kp, lon_kp\n"
                    f"検出ヘッダ: {detected}\n"
                    f"区切り推定: {repr(dialect.delimiter)} / 文字コード: {enc}"
                )

            has_cf = "course_front" in headers; has_cb = "course_back" in headers
            has_kp = "lat_kp" in headers and "lon_kp" in headers
            has_st = "street" in headers

            prog = self.qgis.progress_dialog("CSV を読み込み中…")

            try:
                for i, row in enumerate(rdr, start=2):
                    if i % 2000 == 0:
                        prog.setLabelText(f"{i:,} 行 読み込み中…"); prog.setValue(0)
                        if prog.wasCanceled():
                            raise Exception("ユーザにより中止されました。")
                    try:
                        kp = (row[headers['kp']] or '').strip()
                        pf = (row[headers['pic_front']] or '').strip()
                        pb = (row[headers['pic_back']]  or '').strip()
                        if not pf and not pb and not has_kp:
                            continue
                        street = (row[headers['street']].strip() if has_st else '')
                        lat_kp = parse_float(row[headers['lat_kp']]) if has_kp else None
                        lon_kp = parse_float(row[headers['lon_kp']]) if has_kp else None
                        lat_f = parse_float(row[headers['lat_front']])
                        lon_f = parse_float(row[headers['lon_front']])
                        lat_b = parse_float(row[headers['lat_back']])
                        lon_b = parse_float(row[headers['lon_back']])
                        cf = parse_float(row[headers['course_front']]) if has_cf else None
                        cb = parse_float(row[headers['course_back']])  if has_cb else None
                        rows.append(Row(kp, lat_kp, lon_kp, street, pf, lat_f, lon_f, cf, pb, lat_b, lon_b, cb))
                    except Exception as e:
                        print(f"[WARN] {i}行目スキップ: {e}")
            finally:
                prog.close()
        if not rows:
            raise Exception("CSVから有効な行を読み込めませんでした。")
        return rows

    # ------------- レイヤ確保/初期化 -------------
    def _ensure_points_fields(self, lyr: QgsVectorLayer):
        prov = lyr.dataProvider()
        names = lyr.fields().names()
        new_fields = []
        if "is_show" not in names:
            new_fields.append(QgsField("is_show", QVariant.Int))
        if new_fields:
            with EditContext(lyr):
                prov.addAttributes(new_fields)
                lyr.updateFields()

    def _ensure_point_layer(self) -> QgsVectorLayer:
        if self.layer and self.layer.isValid():
            return self.layer
        self.layer = self.qgis.ensure_point_layer(self.LAYER_NAME)
        self._ensure_points_fields(self.layer)
        self._hook_layer(self.layer)
        return self.layer

    # ------------- 表示制御 -------------
    def _on_resized(self, side: str):
        key = (side, self.current_index)
        pix = self._pix_cache.get(key)
        if not pix or pix.isNull():
            return
        if side == "front":
            self.ui.set_front_pixmap(pix)
        else:
            self.ui.set_back_pixmap(pix)

    def _set_pixmap(self, side: str, key: Tuple[str, int], path: Path):
        if not path.is_file():
            if side == "front":
                self.ui.set_front_pixmap(None, f"画像が見つかりません:\n{path}")
            else:
                self.ui.set_back_pixmap(None, f"画像が見つかりません:\n{path}")
            return
        pix = QPixmap(str(path))
        if pix.isNull():
            if side == "front":
                self.ui.set_front_pixmap(None, f"画像を開けませんでした:\n{path}")
            else:
                self.ui.set_back_pixmap(None, f"画像を開けませんでした:\n{path}")
            return
        self._pix_cache[key] = pix
        if side == "front":
            self.ui.set_front_pixmap(pix)
        else:
            self.ui.set_back_pixmap(pix)

    def _update_name_labels(self, row, disp_front: Optional[str], disp_back: Optional[str]):
        p_front = self._resolve_image_path((disp_front if disp_front is not None else row.front) or "")
        p_back  = self._resolve_image_path((disp_back  if disp_back  is not None else row.back)  or "")
        self.ui.set_name_labels(p_front.name, str(p_front), p_back.name, str(p_back), row.kp)

    def _resolve_image_path(self, path_like: str) -> Path:
        s = str(path_like or "").strip()
        p = Path(s)
        if not p.is_absolute():
            p = self.img_dir / p
        return p

    def _select_features(self, feats: List[QgsFeature]):
        if not (self.layer and feats):
            return
        try:
            self.suspend_selection_signal = True
            ids = [f.id() for f in feats if f is not None]
            self.qgis.select_by_ids(self.layer, ids)
        finally:
            self.suspend_selection_signal = False

        if self.auto_zoom and feats:
            try:
                self.qgis.zoom_to_feature_ids(self.layer, [f.id() for f in feats], scale=500)
            except Exception:
                pass

    def _find_feature_by_pic_or_coord(self, pic: Optional[str], lat: Optional[float], lon: Optional[float], expected_side: Optional[str] = None):
        if not self.layer:
            return None
        key = (pic or "").strip().lower()
        tol = self.COORD_TOL

        if key:
            for f in self.layer.getFeatures():
                try:
                    if expected_side and (f["side"] or "").strip().lower() != expected_side:
                        continue
                    jpg = (f["jpg"] or "").strip().lower()
                    if jpg == key:
                        return f
                except Exception:
                    pass

        if lat is not None and lon is not None:
            for f in self.layer.getFeatures():
                try:
                    if expected_side and (f["side"] or "").strip().lower() != expected_side:
                        continue
                    pt = f.geometry().asPoint()
                    if abs(pt.x() - lon) <= tol and abs(pt.y() - lat) <= tol:
                        return f
                except Exception:
                    pass
        return None

    def _update_is_show_flags(self, front_feat, back_feat):
        if not (self.layer and self.layer.isValid()):
            return
        flds = self.layer.fields()
        idx_show = flds.indexFromName("is_show")
        if idx_show < 0:
            return
        fid_front = front_feat.id() if front_feat else None
        fid_back  = back_feat.id() if back_feat else None
        with EditContext(self.layer):
            for f in self.layer.getFeatures():
                want = 1 if (f.id() == fid_front or f.id() == fid_back) else 0
                cur  = int(f[idx_show] or 0)
                if cur != want:
                    self.layer.changeAttributeValue(f.id(), idx_show, want)
        try:
            self.layer.triggerRepaint()
        except Exception:
            pass

    # ------------- 画像／レコード操作 -------------
    def show_image(self, idx: int):
        if not self.images:
            self.ui.set_front_pixmap(None, "CSVが未読み込みです。⚙設定から指定してください。")
            self.ui.set_back_pixmap(None,  "CSVが未読み込みです。⚙設定から指定してください。")
            self.ui.set_name_labels("","", "","", "—")
            return

        self.current_index = idx % len(self.images)
        row = self.images[self.current_index]

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

        def _same_street(a, b):
            return getattr(a, "street", "") == getattr(b, "street", "")

        def _pick_from_row(r, side: str):
            if side == "front":
                return r.front, "front", r.lat_front, r.lon_front
            else:
                return r.back, "back", r.lat_back, r.lon_back

        # left(front)
        if (row.lat_kp is not None) and (row.lon_kp is not None) and prev_row and _same_street(prev_row, row):
            cand = [_pick_from_row(prev_row, "front"), _pick_from_row(prev_row, "back")]
        else:
            cand = [_pick_from_row(row, "front")]
        disp_front, side_front, lat_f_use, lon_f_use = None, "front", None, None
        for pth, sd, la, lo in cand:
            if pth:
                disp_front, side_front, lat_f_use, lon_f_use = pth, sd, la, lo
                break

        # right(back)
        if (row.lat_kp is not None) and (row.lon_kp is not None) and next_row and _same_street(next_row, row):
            cand = [_pick_from_row(next_row, "back"), _pick_from_row(next_row, "front")]
        else:
            cand = [_pick_from_row(row, "back")]
        disp_back, side_back, lat_b_use, lon_b_use = None, "back", None, None
        for pth, sd, la, lo in cand:
            if pth:
                disp_back, side_back, lat_b_use, lon_b_use = pth, sd, la, lo
                break

        # 画像表示
        if disp_front:
            self._set_pixmap("front", ("front", self.current_index), self._resolve_image_path(disp_front))
        else:
            self.ui.set_front_pixmap(None, "（frontなし）")
        if disp_back:
            self._set_pixmap("back", ("back", self.current_index), self._resolve_image_path(disp_back))
        else:
            self.ui.set_back_pixmap(None, "（backなし）")

        self._update_name_labels(row, disp_front, disp_back)

        # レイヤ上の点検索
        feats = []
        ff = self._find_feature_by_pic_or_coord(disp_front, lat_f_use, lon_f_use, expected_side=side_front)
        if ff: feats.append(ff)
        fb = self._find_feature_by_pic_or_coord(disp_back,  lat_b_use, lon_b_use, expected_side=side_back)
        if fb: feats.append(fb)
        if feats:
            self._select_features(feats)
        self._update_is_show_flags(ff, fb)
        self._set_kp_selected(row.kp)

        self._last_disp_front = disp_front
        self._last_disp_back  = disp_back

    def next_image(self):
        self.show_image(self.current_index + 1)

    def prev_image(self):
        self.show_image(self.current_index - 1)

    # ------------- コンフィグ/ロード -------------
    def configure_and_load(self):
        try:
            csv_file, img_dir_sel = self._pick_paths()
            rows = self._load_csv(csv_file)
        except Exception as e:
            self.qgis.error("PhotoViewer 設定エラー", str(e))
            return

        self.images = rows
        self.img_dir = Path(img_dir_sel)
        self._rebuild_index()

        lyr = self._ensure_point_layer()
        self._plot_all_points(lyr)
        self.show_image(0)

    def _rebuild_index(self):
        self._idx_by_kp.clear()
        self._idx_by_pic.clear()
        kp_order: List[Tuple[str, float, int]] = []

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

    def _plot_all_points(self, layer: QgsVectorLayer, clear_existing=True):
        if not self.images:
            self.qgis.info("PhotoViewer", "CSVが未読み込みです。")
            return

        def _info(n):
            self.qgis.info("PhotoViewer", f"プロット完了：{n} 点を追加しました。")

        lyrmod.plot_all_points(layer, self.images, info_cb=_info)
        ext = layer.extent()
        if ext and not ext.isEmpty():
            c = self.qgis.canvas()
            c.setExtent(ext); c.refresh()

    # ------------- 選択連動 -------------
    def _hook_layer(self, layer_obj: QgsVectorLayer):
        try:
            layer_obj.selectionChanged.disconnect(self._on_layer_selection_changed)
        except Exception:
            pass
        layer_obj.selectionChanged.connect(self._on_layer_selection_changed)

    def _on_layer_will_be_removed(self, layer_id: str):
        if self.layer and self.layer.id() == layer_id:
            self.layer = None

    def _on_layer_selection_changed(self, *args):
        if self.suspend_selection_signal or not self.layer or not self.images:
            return
        sel = list(self.layer.selectedFeatures())
        if not sel:
            return
        f = sel[0]
        try:
            kp_val = (f["kp"] or "").strip().lower()
            if kp_val and kp_val in self._idx_by_kp:
                self.show_image(self._idx_by_kp[kp_val]); return
        except Exception:
            pass
        for key in ("jpg", "pic_front", "pic_back"):
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

    # ------------- クリック追加（PhotoClicks） -------------
    def _ensure_click_layer(self) -> Optional[QgsVectorLayer]:
        lyr = self.qgis.ensure_click_layer(self.CLICK_LAYER_NAME)
        if not lyr:
            self.ui.set_add_mode_text(False)
            return None
        self._ensure_click_fields(lyr)
        self.click_layer = lyr
        return lyr

    @staticmethod
    def _ensure_click_fields(lyr: QgsVectorLayer):
        prov = lyr.dataProvider()
        names = lyr.fields().names()
        new_fields = []
        if "lat" not in names: new_fields.append(QgsField("lat", QVariant.Double))
        if "lon" not in names: new_fields.append(QgsField("lon", QVariant.Double))
        if "jpg" not in names: new_fields.append(QgsField("jpg", QVariant.String))
        if new_fields:
            with EditContext(lyr):
                prov.addAttributes(new_fields); lyr.updateFields()

    def _toggle_add_mode(self):
        if getattr(self, "_add_mode", False):
            self._disable_add_mode()
        else:
            self._enable_add_mode()

    def _enable_add_mode(self):
        if getattr(self, "_del_mode", False):
            self._disable_del_mode()
        lyr = self._ensure_click_layer()
        if not lyr:
            self.ui.set_add_mode_text(False); return
        canvas = self.qgis.canvas()
        self._prev_map_tool = canvas.mapTool()
        self._click_tool = maptools.AddPointTool(self, canvas, lyr)
        canvas.setMapTool(self._click_tool)
        self._add_mode = True
        self.ui.set_add_mode_text(True)

    def _disable_add_mode(self):
        canvas = self.qgis.canvas()
        if getattr(self, "_prev_map_tool", None):
            canvas.setMapTool(self._prev_map_tool)
        self._click_tool = None; self._prev_map_tool = None; self._add_mode = False
        self.ui.set_add_mode_text(False)

    def _toggle_del_mode(self):
        if getattr(self, "_del_mode", False):
            self._disable_del_mode()
        else:
            self._enable_del_mode()

    def _enable_del_mode(self):
        if getattr(self, "_add_mode", False):
            self._disable_add_mode()
        lyr = self._ensure_click_layer()
        if not lyr:
            self.ui.set_del_mode_text(False); return
        canvas = self.qgis.canvas()
        self._prev_map_tool = canvas.mapTool()
        self._delete_tool = maptools.DeletePointTool(self, canvas, lyr)
        canvas.setMapTool(self._delete_tool)
        self._del_mode = True
        self.ui.set_del_mode_text(True)

    def _disable_del_mode(self):
        canvas = self.qgis.canvas()
        if getattr(self, "_prev_map_tool", None):
            canvas.setMapTool(self._prev_map_tool)
        self._delete_tool = None; self._prev_map_tool = None; self._del_mode = False
        self.ui.set_del_mode_text(False)

    def _export_clicks_csv(self):
        lyr = getattr(self, "click_layer", None) or self._ensure_click_layer()
        if not lyr or not lyr.isValid():
            self.qgis.warn("CSV保存（Clicks）", "出力対象の PhotoClicks レイヤがありません。")
            return

        last_path = settings.value(self.SKEY_LAST_EXPORT_CLICKS, "", type=str) if hasattr(self, "SKEY_LAST_EXPORT_CLICKS") else ""
        if not last_path:
            base_dir = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) \
                       or QStandardPaths.writableLocation(QStandardPaths.TempLocation)
            last_path = str(Path(base_dir) / "photo_clicks.csv")

        out_csv = self.qgis.ask_export_csv_path(last_path)
        if not out_csv:
            return

        p = Path(out_csv)
        if p.suffix.lower() != ".csv":
            out_csv = str(p.with_suffix(".csv"))

        sel_only = False
        if lyr.selectedFeatureCount() > 0:
            from qgis.PyQt.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                iface.mainWindow(),
                "CSV保存（Clicks）",
                "選択フィーチャのみを書き出しますか？\n「いいえ」を選ぶと全件を書き出します。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            sel_only = (reply == QMessageBox.Yes)

        try:
            Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
            self.qgis.export_layer_csv(lyr, out_csv, only_selected=sel_only)
            if hasattr(self, "SKEY_LAST_EXPORT_CLICKS"):
                settings.setValue(self.SKEY_LAST_EXPORT_CLICKS, out_csv)
            self.qgis.info("CSV保存（Clicks）", f"保存しました:\n{out_csv}")
        except Exception as e:
            self.qgis.error(
                "CSV保存エラー（Clicks）",
                f"保存に失敗しました。\n{e}\n\n"
                "書き込み可能なフォルダ（ドキュメント等）への保存や、"
                "ファイルを開いているアプリ(Excel等)を閉じるなどを試してください。"
            )

    # ------------- 補助 -------------
    def _set_kp_selected(self, kp_value: str):
        if not self.layer or not kp_value:
            return
        try:
            idx_is_sel = self.layer.fields().indexFromName("is_sel")
            idx_side   = self.layer.fields().indexFromName("side")
            idx_kp     = self.layer.fields().indexFromName("kp")
            if min(idx_is_sel, idx_side, idx_kp) < 0:
                return
            with EditContext(self.layer):
                for f in self.layer.getFeatures():
                    try:
                        if (str(f[idx_side]).strip().lower() == "kp"):
                            want = 1 if (str(f[idx_kp]).strip().lower() == str(kp_value).strip().lower()) else 0
                            cur  = int(f[idx_is_sel] or 0)
                            if cur != want:
                                self.layer.changeAttributeValue(f.id(), idx_is_sel, want)
                    except Exception:
                        pass
            self.layer.triggerRepaint()
        except Exception as e:
            print("_set_kp_selected error:", e)

    def _jump(self, key_text: str):
        key = key_text.strip().lower()
        if not key:
            return
        i = self._idx_by_kp.get(key)
        if i is None:
            i = self._idx_by_pic.get(key)
        if i is None:
            self.qgis.info("ジャンプ", f"見つかりませんでした: {key}")
            return
        self.show_image(i)

    def _on_image_dblclick(self, side: str):
        if not self.images:
            return
        pth = getattr(self, "_last_disp_front" if side == "front" else "_last_disp_back", None)
        if pth:
            p = self._resolve_image_path(pth)
            if p.is_file():
                self.qgis.open_local_file(p)

    def _save_autoz(self, checked: bool):
        self.auto_zoom = bool(checked)
        settings.setValue(SKEY_AUTZOOM, self.auto_zoom)

    def _on_dock_destroyed(self, *args):
        try:
            settings.setValue(SKEY_GEOM, self.ui.dockwidget().saveGeometry())
        except Exception:
            pass
        if getattr(self, "_add_mode", False):
            self._disable_add_mode()
        if getattr(self, "_del_mode", False):
            self._disable_del_mode()
