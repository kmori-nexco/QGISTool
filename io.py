#io.py
from __future__ import annotations
import csv, json
from pathlib import Path
from typing import List, Optional, Callable, Tuple, Dict

from qgis.core import QgsFeature, QgsGeometry

from .utils import (
    Row, open_with_fallback, parse_float, header_map, normalize_header,
    detect_csv_dialect, transform_point, export_layer_to_csv, resolve_path,EditContext
)
from .fields import FN

# ========== 画像CSVのロード（UI依存なし） ==========
def load_images_csv(csv_path: str, on_progress: Optional[Callable[[int], None]] = None) -> List[Row]:
    rows: List[Row] = []
    f, enc = open_with_fallback(csv_path)
    with f:
        dialect = detect_csv_dialect(f)
        rdr = csv.DictReader(f, dialect=dialect)
        headers = header_map(rdr.fieldnames or [])

        required = {"kp", "pic_front", "lat_front", "lon_front", "pic_back", "lat_back", "lon_back"}
        missing = sorted(required - set(headers.keys()))
        if missing:
            detected = ", ".join([normalize_header(h) for h in (rdr.fieldnames or [])])
            raise Exception(
                "Missing required CSV headers.\n"
                f"Missing: {', '.join(missing)}\n"
                "Required: kp, pic_front, lat_front, lon_front, pic_back, lat_back, lon_back\n"
                "Optional: course_front, course_back, lat_kp, lon_kp, street\n"
                f"Detected headers: {detected}\n"
                f"Detected delimiter: {repr(dialect.delimiter)} / Encoding: {enc}"
            )

        has_cf = "course_front" in headers
        has_cb = "course_back" in headers
        has_kp = "lat_kp" in headers and "lon_kp" in headers
        has_st = "street" in headers

        for i, row in enumerate(rdr, start=2):
            if on_progress and (i % 2000 == 0):
                on_progress(i)
            try:
                kp = (row[headers["kp"]] or "").strip()
                pf = (row[headers["pic_front"]] or "").strip()
                pb = (row[headers["pic_back"]] or "").strip()
                if not pf and not pb and not has_kp:
                    continue
                street = (row[headers["street"]].strip() if has_st else "")
                lat_kp = parse_float(row[headers["lat_kp"]]) if has_kp else None
                lon_kp = parse_float(row[headers["lon_kp"]]) if has_kp else None
                lat_f = parse_float(row[headers["lat_front"]])
                lon_f = parse_float(row[headers["lon_front"]])
                lat_b = parse_float(row[headers["lat_back"]])
                lon_b = parse_float(row[headers["lon_back"]])
                cf = parse_float(row[headers["course_front"]]) if has_cf else None
                cb = parse_float(row[headers["course_back"]]) if has_cb else None
                rows.append(Row(kp, lat_kp, lon_kp, street, pf, lat_f, lon_f, cf, pb, lat_b, lon_b, cb))
            except Exception as e:
                print(f"[io.load_images_csv] Skipped line {i}: {e}")
    if not rows:
        raise Exception("No valid rows could be read from the CSV")
    return rows

# ========== Clicks のエクスポート（UI依存なし） ==========
def export_clicks_csv(layer, out_csv: str, only_selected: bool, meta: Optional[Dict] = None) -> Tuple[str, Optional[str]]:
    p = Path(out_csv)
    if p.suffix.lower() != ".csv":
        out_csv = str(p.with_suffix(".csv"))
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)

    export_layer_to_csv(layer, out_csv, only_selected=only_selected)

    meta_path = None
    if meta is not None:
        meta_path = str(Path(out_csv)) + ".meta.json"
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False, indent=2)

    return out_csv, meta_path


# ========== Clicks のインポート（UI依存なし） ==========
def import_clicks_csv(
    layer,
    csv_path: str,
    *,
    dst_crs,
    clear: bool = True,
    on_progress: Optional[Callable[[int], None]] = None,
) -> Tuple[int, int, Optional[str]]:
    """
    Returns: (added, skipped, target_kp_from_meta)
    """
    # メタ情報（前回KP）
    meta_path = str(Path(csv_path)) + ".meta.json"
    target_kp = None
    pmeta = Path(meta_path)
    if pmeta.is_file():
        try:
            with open(pmeta, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
                target_kp = (meta.get("kp") or "").strip()
        except Exception:
            target_kp = None

    # CSV読み込み
    f, enc = open_with_fallback(csv_path)
    added = 0
    skipped = 0

    with f:
        dialect = detect_csv_dialect(f)
        rdr = csv.DictReader(f, dialect=dialect)
        headers = header_map(rdr.fieldnames or [])
        required = {"lat", "lon"}
        missing = sorted(required - set(headers.keys()))
        if missing:
            detected = ", ".join([normalize_header(h) for h in (rdr.fieldnames or [])])
            raise Exception(
                "Missing required CSV headers.\n"
                f"Missing: {', '.join(missing)}\n"
                "Required: lat, lon\n"
                f"Detected headers: {detected}\n"
                f"Detected delimiter: {repr(dialect.delimiter)} / Encoding: {enc}"
            )

        if clear:
            with EditContext(layer):
                ids = [f.id() for f in layer.getFeatures()]
                if ids:
                    layer.deleteFeatures(ids)

        field_names = layer.fields().names()

        def _pf(val: str):
            try:
                return float((val or "").strip())
            except Exception:
                return None

        def _gs(row, *keys):
            for k in keys:
                if k in headers:
                    try:
                        return (row[headers[k]] or "").strip()
                    except Exception:
                        pass
            return ""

        with EditContext(layer):
            for i, row in enumerate(rdr, start=2):
                if on_progress and (i % 2000 == 0):
                    on_progress(i)

                try:
                    lat = _pf(row[headers["lat"]]) if "lat" in headers else None
                    lon = _pf(row[headers["lon"]]) if "lon" in headers else None
                    if lat is None or lon is None:
                        skipped += 1
                        continue

                    jpg = _gs(row, "jpg")
                    cat = (_gs(row, "category") or "").strip().lower()
                    ts = (_gs(row, "trafficsign", "traffic_sign", "traffic sign") or "").strip().lower()
                    pl = (_gs(row, "pole") or "").strip().lower()
                    fh = (_gs(row, "fire_hydrant", "fire hydrant") or "").strip().lower()
                    unk = (_gs(row, "unknown", "unk") or "").strip().lower()
                    sc = (_gs(row, "subcat") or "").strip().lower()

                    try:
                        pt = transform_point(lat, lon, dst_crs=dst_crs)
                    except Exception:
                        skipped += 1
                        continue

                    feat = QgsFeature(layer.fields())
                    feat.setGeometry(QgsGeometry.fromPointXY(pt))

                    attrs = {}
                    if FN.LAT in field_names: attrs[FN.LAT] = lat
                    if FN.LON in field_names: attrs[FN.LON] = lon
                    if FN.JPG in field_names: attrs[FN.JPG] = jpg
                    if FN.CATEGORY in field_names: attrs[FN.CATEGORY] = cat
                    if FN.TRAFFIC_SIGN in field_names: attrs[FN.TRAFFIC_SIGN] = ts
                    if FN.POLE in field_names: attrs[FN.POLE] = pl
                    if FN.FIREHYDRANT in field_names: attrs[FN.FIREHYDRANT] = fh
                    if FN.UNKNOWN in field_names: attrs[FN.UNKNOWN] = unk
                    if "subcat" in field_names: attrs["subcat"] = sc

                    feat.setAttributes([attrs.get(n, None) for n in field_names])
                    if not layer.addFeature(feat):
                        skipped += 1
                        continue
                    added += 1
                except Exception:
                    skipped += 1
                    continue

    layer.triggerRepaint()
    return added, skipped, target_kp
