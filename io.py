#io.py
from __future__ import annotations
import csv
from pathlib import Path
from typing import Optional, Callable, Tuple

from qgis.core import QgsFeature, QgsGeometry

from .utils import (
    open_with_fallback, header_map, normalize_header,
    detect_csv_dialect, transform_point, export_layer_to_csv, EditContext
)
from .fields import FN

# ========== Clicks のエクスポート（UI依存なし） ==========
def export_clicks_csv(layer, out_csv: str, only_selected: bool) -> str:
    p = Path(out_csv)
    if p.suffix.lower() != ".csv":
        out_csv = str(p.with_suffix(".csv"))
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)

    export_layer_to_csv(layer, out_csv, only_selected=only_selected)

    return out_csv


# ========== Clicks のインポート（UI依存なし） ==========
def import_clicks_csv(
    layer,
    csv_path: str,
    *,
    dst_crs,
    clear: bool = True,
    on_progress: Optional[Callable[[int], None]] = None,
) -> Tuple[int, int]:
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
    return added, skipped
