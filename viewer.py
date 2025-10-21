import re, csv
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from qgis.PyQt.QtGui import QPixmap, QKeySequence, QColor, QDesktopServices
from qgis.PyQt.QtCore import Qt, QVariant, QUrl
from qgis.PyQt.QtWidgets import (
    QFileDialog, QDockWidget, QLabel, QVBoxLayout, QWidget, QPushButton,
    QHBoxLayout, QMessageBox, QShortcut, QSizePolicy, QLineEdit, QCheckBox,
    QFormLayout, QDialog, QDialogButtonBox, QComboBox,
)

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsMapLayer, QgsMarkerSymbol, QgsProperty,
    QgsSvgMarkerSymbolLayer, QgsApplication,
    QgsCategorizedSymbolRenderer, QgsRendererCategory,
    QgsFontMarkerSymbolLayer, QgsSymbolLayer
)
from qgis.gui import QgsMapTool
from qgis.utils import iface

from .utils import (
    Row, EditContext, settings,
    SKEY_ROOT, SKEY_CSV, SKEY_IMG, SKEY_GEOM, SKEY_AUTZOOM,
    open_with_fallback, parse_float, header_map, normalize_header,
)

class PhotoViewerPlus:
    LAYER_NAME = "PhotoPoints"
    CLICK_LAYER_NAME = "PhotoClicks"

    def __init__(self):
        self.images: List[Row] = []
        self.img_dir = Path()
        self.layer = None
        self._pix_cache: Dict[Tuple[str, int], QPixmap] = {}
        self.current_index = 0
        self.suspend_selection_signal = False
        self.COORD_TOL = 1e-7
        self.auto_zoom = bool(settings.value(SKEY_AUTZOOM, True, type=bool))
        self._idx_by_kp: Dict[str, int] = {}
        self._idx_by_pic: Dict[str, int] = {}
        self.USER_ATTRS: List[Tuple[str, Optional[List[str]]]] = [
            ("Traffic Sign", ["Stop","Do not Enter","Other"]),
            ("Poll", ["Utility","Light"]),
            ("drain inlit", ["drain inlit"]),
        ]

        self._build_ui()
        iface.mapCanvas().setSelectionColor(QColor(0, 0, 0, 255))

    class _AttrDialog(QDialog):
        def __init__(self, parent, attrs_spec: List[Tuple[str, Optional[List[str]]]], last_values: Dict[str, str]):
            super().__init__(parent)
            self.setWindowTitle("属性を選択")
            self.rows = []
            lay = QVBoxLayout(self)
            form = QFormLayout()
            for name, options in attrs_spec:
                chk = QCheckBox(name)
                if options:
                    editor = QComboBox(); editor.addItems(options)
                else:
                    editor = QLineEdit()
                val = last_values.get(name, "")
                if isinstance(editor, QComboBox) and val:
                    i = editor.findText(val)
                    if i >= 0: editor.setCurrentIndex(i)
                elif isinstance(editor, QLineEdit):
                    editor.setText(val)
                editor.setEnabled(False)
                chk.toggled.connect(editor.setEnabled)
                form.addRow(chk, editor)
                self.rows.append((name, chk, editor))
            lay.addLayout(form)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
            lay.addWidget(bb)

        def values(self) -> Dict[str, str]:
            out = {}
            for name, chk, editor in self.rows:
                if chk.isChecked():
                    if isinstance(editor, QComboBox):
                        out[name] = editor.currentText().strip()
                    else:
                        out[name] = editor.text().strip()
            return out

    # UI
    def _build_ui(self):
        for w in iface.mainWindow().findChildren(QDockWidget):
            if w.objectName() == "PhotoViewerDockPlus":
                w.close(); w.deleteLater()

        self.dock = QDockWidget("画像ビューア＋", iface.mainWindow())
        self.dock.setObjectName("PhotoViewerDockPlus")
        root = QWidget(); self.root = root
        layout_root = QVBoxLayout(root)
        layout_root.setContentsMargins(6, 6, 6, 6)
        layout_root.setSpacing(4)

        # 画像ラベル
        self.img_label_front = QLabel("⚙ でCSVと画像フォルダを選択してください")
        self.img_label_back  = QLabel("⚙ でCSVと画像フォルダを選択してください")
        for lab in (self.img_label_front, self.img_label_back):
            lab.setAlignment(Qt.AlignCenter)
            lab.setMinimumSize(420, 280)
            lab.setScaledContents(False)
            lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            lab.setStyleSheet("border: 1px solid #999; background-color:#fdfdfd;")
            lab.mouseDoubleClickEvent = self._on_image_dblclick

        # 見出し付きボックス
        def titled_box(title, label, color):
            box = QVBoxLayout(); t = QLabel(title)
            t.setAlignment(Qt.AlignCenter)
            t.setStyleSheet(f"font-weight:bold; color:{color}; font-size:11pt;")
            box.addWidget(t); box.addWidget(label, 1)
            return box

        img_area = QVBoxLayout()
        img_area.addLayout(titled_box("Front（前方）", self.img_label_front, "#0078d7"), 1)
        img_area.addLayout(titled_box("Back（後方）",  self.img_label_back,  "#d74100"), 1)

        # ファイル名 / KP
        self.name_label_front = QLabel("—"); self.name_label_back  = QLabel("—")
        for lab in (self.name_label_front, self.name_label_back):
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet("color:#888; font-family: Menlo, 'Courier New', monospace; font-size:10px; padding:2px;")
            lab.setMaximumHeight(18)
            lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        names_area = QVBoxLayout(); names_area.addWidget(self.name_label_front); names_area.addWidget(self.name_label_back)

        # 操作列
        btns = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 前へ"); self.next_btn = QPushButton("次へ ▶")
        self.cfg_btn  = QPushButton("⚙ 設定")
        self.add_btn  = QPushButton("● クリック追加"); self.add_btn.setCheckable(True)
        self.add_btn.setToolTip("ONにすると、地図クリックでPhotoClicksにポイントを追加します")
        self.del_btn  = QPushButton("✖ クリック削除"); self.del_btn.setCheckable(True)
        self.del_btn.setToolTip("ONにすると、地図クリックでPhotoClicksのポイントを削除します")
        self.zoom_chk = QCheckBox("選択時に自動ズーム"); self.zoom_chk.setChecked(self.auto_zoom)
        for b in (self.prev_btn, self.next_btn, self.cfg_btn, self.add_btn, self.del_btn):
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        for w in (self.prev_btn, self.next_btn, self.cfg_btn, self.add_btn, self.del_btn, self.zoom_chk):
            btns.addWidget(w)

        # クイック検索
        quick_area = QHBoxLayout(); self.q_edit = QLineEdit(); self.q_edit.setPlaceholderText("KP または 画像名でジャンプ… Enter で確定")
        self.q_btn = QPushButton("移動"); quick_area.addWidget(self.q_edit, 1); quick_area.addWidget(self.q_btn)

        # レイアウト合成
        layout_root.addLayout(img_area, 1)
        layout_root.addLayout(names_area, 0)
        layout_root.addLayout(btns, 0)
        layout_root.addLayout(quick_area, 0)

        self.dock.setWidget(root); iface.addDockWidget(Qt.RightDockWidgetArea, self.dock); self.dock.show()

        # ショートカット
        QShortcut(QKeySequence(Qt.Key_Left),  root, activated=self.prev_image)
        QShortcut(QKeySequence(Qt.Key_Right), root, activated=self.next_image)

        # イベント
        self.prev_btn.clicked.connect(self.prev_image)
        self.next_btn.clicked.connect(self.next_image)
        self.cfg_btn.clicked.connect(self.configure_and_load)
        self.add_btn.clicked.connect(self._toggle_add_mode)
        self.del_btn.clicked.connect(self._toggle_del_mode)
        self.q_btn.clicked.connect(self._jump)
        self.q_edit.returnPressed.connect(self._jump)
        self.zoom_chk.toggled.connect(self._save_autoz)

        # リサイズ
        self.img_label_front.resizeEvent = lambda e: self._resized(self.img_label_front, key=("front", self.current_index), ev=e)
        self.img_label_back.resizeEvent  = lambda e: self._resized(self.img_label_back,  key=("back",  self.current_index), ev=e)

        # 破棄時
        self.dock.destroyed.connect(self._on_dock_destroyed)
        try:
            geom = settings.value(SKEY_GEOM, None)
            if geom:
                self.dock.restoreGeometry(geom)
        except Exception:
            pass

    # --- 以下、元コードのロジックをそのまま移植（若干の関数名だけ utils 参照に変更） ---
    def _pick_paths(self) -> Tuple[str, str]:
        last_csv = settings.value(SKEY_CSV, '', type=str) or ''
        last_img = settings.value(SKEY_IMG, '', type=str) or ''
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
        settings.setValue(SKEY_CSV, csv_file); settings.setValue(SKEY_IMG, img_dir)
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

            from qgis.PyQt.QtWidgets import QProgressDialog
            prog = QProgressDialog("CSV を読み込み中…", "中止", 0, 0, iface.mainWindow())
            prog.setWindowModality(Qt.ApplicationModal); prog.setMinimumDuration(400)

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
    def _ensure_point_layer(self):
        if self.layer and self.layer.isValid():
            return self.layer
        uri = (
            "Point?crs=epsg:4326"
            "&field=kp:string&field=side:string&field=jpg:string"
            "&field=street:string"
            "&field=pic_front:string&field=pic_back:string"
            "&field=lat:double&field=lon:double&field=course:double"
        )
        lyr = QgsVectorLayer(uri, self.LAYER_NAME, "memory")
        if not lyr.isValid():
            raise Exception("ポイントレイヤの作成に失敗しました。")
        QgsProject.instance().addMapLayer(lyr)
        self.layer = lyr
        self._apply_plane_symbology()
        self._hook_layer(self.layer)
        return self.layer

    def _apply_plane_symbology(self, size: float = 14.0, angle_offset: float = 0.0, prefer_font=True, plane_svg_path: Optional[str]=None):
        try:
            plane_svg = None
            if not prefer_font:
                if plane_svg_path and Path(plane_svg_path).exists():
                    plane_svg = plane_svg_path
                else:
                    plane_svg = self._find_builtin_plane_svg()

            def _make_symbol(color: QColor):
                sym = QgsMarkerSymbol()
                if plane_svg:
                    svg = QgsSvgMarkerSymbolLayer(plane_svg, size)
                    try: svg.setColor(color)
                    except Exception: pass
                    try: svg.setFillColor(color)
                    except Exception: pass
                    try:
                        svg.setOutlineColor(QColor(0,0,0,180)); svg.setOutlineWidth(0.3)
                    except Exception: pass
                    sym.changeSymbolLayer(0, svg)
                    lyr = sym.symbolLayer(0)
                else:
                    fm = QgsFontMarkerSymbolLayer()
                    fm.setFontFamily("Arial"); fm.setCharacter("✈"); fm.setColor(color); fm.setSize(size)
                    sym.changeSymbolLayer(0, fm)
                    lyr = sym.symbolLayer(0)
                expr = "case when \"course\" is null then 90 else 90 - \"course\" end + ({})".format(float(angle_offset))
                try:
                    lyr.setDataDefinedProperty(QgsSymbolLayer.PropertyAngle, QgsProperty.fromExpression(expr))
                except Exception:
                    sym.setDataDefinedAngle(QgsProperty.fromExpression(expr))
                return sym
                
            def _make_kp_symbol():
                sym = QgsMarkerSymbol.createSimple({
                    "name": "circle",
                    "size": "3.0",
                    "outline_color": "0,0,0,200",
                    "outline_width": "0.4",
                    "color": "180,0,255,220"  # 目立つパープル系（好みで変更OK）
                })
                return sym

            cats = [
                QgsRendererCategory("front", _make_symbol(QColor(0,120,255)), "front"),
                QgsRendererCategory("back",  _make_symbol(QColor(255,80,0)),   "back"),
                QgsRendererCategory("kp",    _make_kp_symbol(),                "kp"),
            ]
            renderer = QgsCategorizedSymbolRenderer("side", cats)
            self.layer.setRenderer(renderer)
            self.layer.triggerRepaint()
        except Exception as e:
            print("_apply_plane_symbology error:", e)

    @staticmethod
    def _find_builtin_plane_svg() -> Optional[str]:
        candidates = [
            "transport/transport_airport.svg", "transport/airplane.svg", "transport/airport.svg", "transport/plane.svg",
            "symbols/transport/transport_airport.svg", "symbols/transport/airplane.svg",
        ]
        try:
            for base in QgsApplication.svgPaths():
                for name in candidates:
                    p = Path(base) / name
                    if p.exists():
                        return str(p)
        except Exception:
            pass
        return None

    # -------------- 表示制御 --------------
    def _resized(self, label: QLabel, key: Tuple[str, int], ev=None):
        # ラベル幅にフィット
        pix = self._pix_cache.get(key)
        if pix and not pix.isNull():
            w = max(1, label.width())
            label.setPixmap(pix.scaledToWidth(w, Qt.SmoothTransformation))
        if ev: QLabel.resizeEvent(label, ev)

    def _set_pixmap(self, label: QLabel, key: Tuple[str, int], path: Path):
        if not path.is_file():
            label.setText(f"画像が見つかりません:\n{path}"); return
        pix = QPixmap(str(path))
        if pix.isNull():
            label.setText(f"画像を開けませんでした:\n{path}"); return
        self._pix_cache[key] = pix
        w = max(1, label.width())
        label.setPixmap(pix.scaledToWidth(w, Qt.SmoothTransformation))

    def _update_name_labels(self, row: Row, disp_front: Optional[str] = None, disp_back: Optional[str] = None):
        p_front = self._resolve_image_path((disp_front if disp_front is not None else row.front) or "")
        p_back  = self._resolve_image_path((disp_back  if disp_back  is not None else row.back)  or "")

        self.name_label_front.setText(f"{p_front.name if p_front.name else '—'}  (KP:{row.kp})")
        self.name_label_front.setToolTip(str(p_front))

        self.name_label_back.setText(f"{p_back.name if p_back.name else '—'}  (KP:{row.kp})")
        self.name_label_back.setToolTip(str(p_back))

    def _resolve_image_path(self, path_like: str) -> Path:
        s = str(path_like or "").strip()
        p = Path(s)
        if not p.is_absolute():
            p = self.img_dir / p
        return p
        
    def _select_features(self, feats):
        if not (self.layer and feats):
            return
        try:
            self.suspend_selection_signal = True
            self.layer.removeSelection()
            ids = [f.id() for f in feats if f is not None]
            if ids:
                self.layer.selectByIds(ids)
        finally:
            self.suspend_selection_signal = False

        if self.auto_zoom and ids:
            try:
                iface.mapCanvas().zoomToFeatureIds(self.layer, ids)
                iface.mapCanvas().zoomScale(500)  # 地図のズームの調整
                iface.mapCanvas().refresh()
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


    # -------------- 画像／レコード操作 --------------
    def show_image(self, idx: int):
        if not self.images:
            for lab in (self.img_label_front, self.img_label_back):
                lab.setText("CSVが未読み込みです。⚙設定から指定してください。")
            self.name_label_front.setText("—"); self.name_label_front.setToolTip("")
            self.name_label_back.setText("—");  self.name_label_back.setToolTip("")
            return

        self.current_index = idx % len(self.images)
        row = self.images[self.current_index]

        disp_front = row.front
        disp_back  = row.back

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
                disp_back = next_row.back or next_row.front or disp_back

        if disp_front:
            self._set_pixmap(self.img_label_front, ("front", self.current_index), self._resolve_image_path(disp_front))
        else:
            self.img_label_front.setText("（frontなし）")

        if disp_back:
            self._set_pixmap(self.img_label_back, ("back", self.current_index), self._resolve_image_path(disp_back))
        else:
            self.img_label_back.setText("（backなし）")

        self._update_name_labels(row, disp_front, disp_back)

        feats = []
        ff = self._find_feature_by_pic_or_coord(disp_front, row.lat_front, row.lon_front, expected_side="front")
        if ff: feats.append(ff)
        fb = self._find_feature_by_pic_or_coord(disp_back,  row.lat_back,  row.lon_back,  expected_side="back")
        if fb: feats.append(fb)
        if feats:
            self._select_features(feats)


    def next_image(self):
        self.show_image(self.current_index + 1)

    def prev_image(self):
        self.show_image(self.current_index - 1)

    # -------------- コンフィグ／ロード --------------
    def configure_and_load(self):
        try:
            csv_file, img_dir_sel = self._pick_paths()
            rows = self._load_csv(csv_file)
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "PhotoViewer 設定エラー", str(e))
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

    def _plot_all_points(self, layer, clear_existing=True):
        if not self.images:
            QMessageBox.information(iface.mainWindow(), "PhotoViewer", "CSVが未読み込みです。")
            return

        prov = layer.dataProvider()
        idx = {n: layer.fields().indexFromName(n) for n in layer.fields().names()}

        with EditContext(layer):
            if clear_existing:
                layer.deleteFeatures([f.id() for f in layer.getFeatures()])

            new_feats = []
            for r in self.images:
                if (r.lat_kp is not None) and (r.lon_kp is not None):
                    f = QgsFeature(layer.fields())
                    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_kp, r.lat_kp)))
                    f.setAttribute(idx["kp"],        r.kp)
                    f.setAttribute(idx["side"],      "kp")
                    f.setAttribute(idx["jpg"],       "")
                    f.setAttribute(idx["street"],    r.street or "")
                    f.setAttribute(idx["pic_front"], r.front or "")
                    f.setAttribute(idx["pic_back"],  r.back or "")
                    f.setAttribute(idx["lat"],       r.lat_kp)
                    f.setAttribute(idx["lon"],       r.lon_kp)
                    new_feats.append(f)
                    
                if r.front and r.lat_front is not None and r.lon_front is not None:
                    f = QgsFeature(layer.fields())
                    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_front, r.lat_front)))
                    f.setAttribute(idx["kp"],        r.kp)
                    f.setAttribute(idx["side"],      "front")
                    f.setAttribute(idx["jpg"],       r.front)
                    f.setAttribute(idx["street"],    r.street or "")
                    f.setAttribute(idx["pic_front"], r.front)
                    f.setAttribute(idx["pic_back"],  r.back or "")
                    f.setAttribute(idx["lat"],       r.lat_front)
                    f.setAttribute(idx["lon"],       r.lon_front)
                    if r.course_front is not None:
                        f.setAttribute(idx["course"], float(r.course_front))
                    new_feats.append(f)
                    
                if r.back and r.lat_back is not None and r.lon_back is not None:
                    f = QgsFeature(layer.fields())
                    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_back, r.lat_back)))
                    f.setAttribute(idx["kp"],        r.kp)
                    f.setAttribute(idx["side"],      "back")
                    f.setAttribute(idx["jpg"],       r.back)
                    f.setAttribute(idx["street"],    r.street or "")
                    f.setAttribute(idx["pic_front"], r.front or "")
                    f.setAttribute(idx["pic_back"],  r.back)
                    f.setAttribute(idx["lat"],       r.lat_back)
                    f.setAttribute(idx["lon"],       r.lon_back)
                    if r.course_back is not None:
                        f.setAttribute(idx["course"], float(r.course_back))
                    new_feats.append(f)

            ok = prov.addFeatures(new_feats)
            if not ok:
                raise Exception("フィーチャの追加に失敗しました。")

        layer.removeSelection(); layer.triggerRepaint()
        ext = layer.extent()
        if ext and not ext.isEmpty():
            iface.mapCanvas().setExtent(ext); iface.mapCanvas().refresh()
        QMessageBox.information(iface.mainWindow(), "PhotoViewer", f"プロット完了：{len(new_feats)} 点を追加しました。")

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

    def _on_layer_selection_changed(self, *args):
        if self.suspend_selection_signal or not self.layer or not self.images:
            return
        sel = list(self.layer.selectedFeatures())
        if not sel:
            return
        f = sel[0]
        # 1) KP 2) 画像名 3) 座標 で探索
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
            
    def _prompt_attributes(self) -> Dict[str, str]:
        # 前回値を読み出し（名前ごとに保存）
        last: Dict[str, str] = {}
        for name, _ in self.USER_ATTRS:
            last[name] = settings.value(f"{SKEY_ROOT}attr/{name}", "", type=str) or ""

        dlg = PhotoViewerPlus._AttrDialog(iface.mainWindow(), self.USER_ATTRS, last)
        if dlg.exec_() != QDialog.Accepted:
            return {}

        selected = dlg.values()

        # 値を保存（次回初期値）
        for k, v in selected.items():
            settings.setValue(f"{SKEY_ROOT}attr/{k}", v)

        # キーを正規化して返す（既存と衝突するキーは user_ プレフィクス）
        out: Dict[str, str] = {}
        for k, v in selected.items():
            key = normalize_header(k).replace(" ", "_")
            if key in ("lat", "lon", "jpg"):
                key = f"user_{key}"
            out[key] = v
        return out

    def _ensure_extra_fields(self, lyr: QgsVectorLayer, keys: List[str]):
        names = set(lyr.fields().names())
        new_fields = []
        for k in keys:
            if k and k not in names:
                new_fields.append(QgsField(k, QVariant.String))
        if new_fields:
            prov = lyr.dataProvider()
            with EditContext(lyr):
                prov.addAttributes(new_fields)
                lyr.updateFields()

    # -------------- クリック追加（PhotoClicks） --------------
    class _AddPointMapTool(QgsMapTool):
        def __init__(self, owner, canvas, target_layer):
            super().__init__(canvas)
            self.owner = owner
            self.canvas = canvas
            self.target = target_layer
            self.setCursor(Qt.CrossCursor)

        def canvasReleaseEvent(self, event):
            if not self.target or not self.target.isValid():
                QMessageBox.warning(iface.mainWindow(), "PhotoClicks", "ターゲットレイヤが無効です。"); return

            # mapCRS → layerCRS に変換して、レイヤに点を追加
            from qgis.core import QgsCoordinateTransform, QgsProject, QgsGeometry, QgsPointXY, QgsFeature

            map_crs = self.canvas.mapSettings().destinationCrs()
            layer_crs = self.target.crs()

            try:
                xform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
                pt_layer = xform.transform(event.mapPoint())
            except Exception as e:
                QMessageBox.warning(iface.mainWindow(), "PhotoClicks", f"座標変換に失敗: {e}"); return

            # 任意属性ダイアログ
            extra_attrs = self.owner._prompt_attributes()

            # 表示中画像名（任意）
            jpg_val = ""
            try:
                if self.owner.images:
                    jpg_val = Path(self.owner.images[self.owner.current_index].front or "").name
            except Exception:
                pass

            try:
                self.owner._ensure_extra_fields(self.target, list(extra_attrs.keys()))

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
                    QMessageBox.warning(iface.mainWindow(), "PhotoClicks", "ポイントの追加に失敗しました。")
            except Exception as e:
                try: self.target.rollBack()
                except Exception: pass
                QMessageBox.critical(iface.mainWindow(), "PhotoClicks", f"追加時エラー: {e}")
                
    class _DeletePointMapTool(QgsMapTool):
        def __init__(self, owner, canvas, target_layer):
            super().__init__(canvas)
            self.owner = owner
            self.canvas = canvas
            self.target = target_layer
            self.setCursor(Qt.ForbiddenCursor)

        def canvasReleaseEvent(self, event):
            if not self.target or not self.target.isValid():
                QMessageBox.warning(iface.mainWindow(), "PhotoClicks", "ターゲットレイヤが無効です。"); return

            from qgis.core import QgsCoordinateTransform, QgsProject, QgsGeometry, QgsPointXY
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

            jpg_val = ""
            try: jpg_val = str(nearest_f["jpg"] or "")
            except Exception: pass
            reply = QMessageBox.question(
                iface.mainWindow(), "PhotoClicks",
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
                QMessageBox.critical(iface.mainWindow(), "PhotoClicks", f"削除時エラー: {e}")

    def _ensure_click_layer(self):
        # 既存検索
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == self.CLICK_LAYER_NAME and lyr.type() == QgsMapLayer.VectorLayer and lyr.geometryType() == QgsWkbTypes.PointGeometry:
                self._ensure_click_fields(lyr)
                return lyr
        uri = "Point?crs=epsg:4326&field=lat:double&field=lon:double&field=jpg:string"
        lyr = QgsVectorLayer(uri, self.CLICK_LAYER_NAME, "memory")
        if not lyr.isValid():
            QMessageBox.critical(iface.mainWindow(), "PhotoClicks", "クリック追加用レイヤの作成に失敗しました。")
            return None
        QgsProject.instance().addMapLayer(lyr)
        self._ensure_click_fields(lyr)
        return lyr

    @staticmethod
    def _ensure_click_fields(lyr):
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
            self.add_btn.setChecked(False); return
        canvas = iface.mapCanvas()
        self._prev_map_tool = canvas.mapTool()
        self._click_tool = PhotoViewerPlus._AddPointMapTool(self, canvas, lyr)
        canvas.setMapTool(self._click_tool)
        self._add_mode = True
        self.add_btn.setText("● クリック追加（ON）")

    def _disable_add_mode(self):
        canvas = iface.mapCanvas()
        if getattr(self, "_prev_map_tool", None):
            canvas.setMapTool(self._prev_map_tool)
        self._click_tool = None; self._prev_map_tool = None; self._add_mode = False
        self.add_btn.setText("● クリック追加"); self.add_btn.setChecked(False)
        
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
            self.del_btn.setChecked(False); return
        canvas = iface.mapCanvas()
        self._prev_map_tool = canvas.mapTool()
        self._delete_tool = PhotoViewerPlus._DeletePointMapTool(self, canvas, lyr)
        canvas.setMapTool(self._delete_tool)
        self._del_mode = True
        self.del_btn.setText("✖ クリック削除（ON）")

    def _disable_del_mode(self):
        canvas = iface.mapCanvas()
        if getattr(self, "_prev_map_tool", None):
            canvas.setMapTool(self._prev_map_tool)
        self._delete_tool = None; self._prev_map_tool = None; self._del_mode = False
        self.del_btn.setText("✖ クリック削除"); self.del_btn.setChecked(False)

    # -------------- 補助 --------------
    def _jump(self):
        key = self.q_edit.text().strip().lower()
        if not key:
            return
        i = self._idx_by_kp.get(key)
        if i is None:
            i = self._idx_by_pic.get(key)
        if i is None:
            QMessageBox.information(iface.mainWindow(), "ジャンプ", f"見つかりませんでした: {key}")
            return
        self.show_image(i)

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

        for p in [self._resolve_image_path(disp_front or ""), self._resolve_image_path(disp_back or "")]:
            try:
                if p and p.is_file():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
            except Exception:
                pass

    def _save_autoz(self, checked: bool):
        self.auto_zoom = bool(checked)
        settings.setValue(SKEY_AUTZOOM, self.auto_zoom)

    def _on_dock_destroyed(self, *args):
        try:
            settings.setValue(SKEY_GEOM, self.dock.saveGeometry())
        except Exception:
            pass
        if getattr(self, "_add_mode", False):
            self._disable_add_mode()
        if getattr(self, "_del_mode", False):
            self._disable_del_mode()
