#utils.py
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List

from qgis.PyQt.QtCore import QSettings
from qgis.core import (
    QgsVectorFileWriter, QgsCoordinateReferenceSystem,
    QgsProject, QgsCoordinateTransform,)

SKEY_ROOT = "QGISTool/"
SKEY_CSV  = SKEY_ROOT + "last_csv"
SKEY_IMG  = SKEY_ROOT + "last_img_dir"
SKEY_GEOM = SKEY_ROOT + "dock_geom"
SKEY_AUTZOOM = SKEY_ROOT + "auto_zoom"

settings = QSettings()

ENCODINGS = ["utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp932", "utf-8"]

@dataclass
class Row:
    kp: str
    lat_kp: Optional[float]
    lon_kp: Optional[float]
    street: str
    front: str
    lat_front: Optional[float]
    lon_front: Optional[float]
    course_front: Optional[float]
    back: str
    lat_back: Optional[float]
    lon_back: Optional[float]
    course_back: Optional[float]

def normalize_header(h: str) -> str:
    if h is None:
        return ""
    s = h
    for a, b in [
        ("\ufeff", ""), ("\u200b", ""), ("\u200d", ""),
        ("\u00a0", " "), ("\u202f", " "), ("\u3000", " "),
    ]:
        s = s.replace(a, b)
    s = s.strip().lower()
    if len(s) >= 2 and ((s[0] == s[-1]) and s[0] in ("'", '"')):
        s = s[1:-1].strip()
    return s

def header_map(fieldnames: List[str]) -> Dict[str, str]:
    return {normalize_header(h): h for h in (fieldnames or [])}

def parse_float(x: Optional[str]):
    s = (x or '').strip()
    return float(s) if s != '' else None

def open_with_fallback(path: str):
    last_err = None
    for enc in ENCODINGS:
        try:
            f = open(path, 'r', encoding=enc, newline='')
            pos = f.tell(); f.read(1); f.seek(pos)
            return f, enc
        except Exception as e:
            last_err = e
    raise last_err or Exception('Unknown encoding')

class EditContext:
    def __init__(self, layer):
        self.layer = layer
    def __enter__(self):
        if self.layer and not self.layer.isEditable():
            self.layer.startEditing()
        return self.layer
    def __exit__(self, exc_type, exc, tb):
        if not self.layer:
            return
        try:
            if exc_type is None:
                self.layer.commitChanges()
            else:
                self.layer.rollBack()
        except Exception:
            pass

def export_layer_to_csv(layer, out_csv_path: str, only_selected: bool = False):
    if not layer or not layer.isValid():
        raise Exception("Layer is invalid")

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "CSV"
    options.fileEncoding = "UTF-8"
    options.layerOptions = [
        "GEOMETRY=AS_XY",   # X,Y列として出力
        "SEPARATOR=COMMA",
        "CREATE_CSVT=YES"
    ]
    options.onlySelectedFeatures = bool(only_selected)

    dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    ctx = QgsProject.instance().transformContext()
    options.ct = QgsCoordinateTransform(layer.crs(), dest_crs, ctx)
    try:
        options.destinationCrs = dest_crs
    except Exception:
        pass

    res, err = QgsVectorFileWriter.writeAsVectorFormatV2(
        layer, out_csv_path, ctx, options
    )
    if res != QgsVectorFileWriter.NoError:
        raise Exception(f"Failed to export CSV")

def detect_csv_dialect(file_obj, sample_size=8192):
    """CSVの区切り文字を自動推定してcsv.Dialectを返す"""
    sample = file_obj.read(sample_size)
    file_obj.seek(0)
    try:
        return csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
    except Exception:
        import csv as _csv
        if "\t" in sample:
            return _csv.excel_tab
        elif ";" in sample and sample.count(";") > sample.count(","):
            d = _csv.excel
            d.delimiter = ";"
            return d
        else:
            return _csv.excel

def safe_float(val) -> Optional[float]:
    """文字列をfloatに変換（失敗時はNone）"""
    try:
        return float(str(val).strip())
    except Exception:
        return None

def safe_str(row, headers, *keys) -> str:
    for k in keys:
        if k in headers:
            try:
                return (row[headers[k]] or "").strip()
            except Exception:
                pass
    return ""

def transform_point(lat, lon, src_epsg="EPSG:4326", dst_crs=None):
    """QgsPointXYをdst_crsに変換（必要な場合のみ）"""
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsPointXY
    pt = QgsPointXY(lon, lat)
    if not dst_crs or not dst_crs.isValid():
        return pt
    src = QgsCoordinateReferenceSystem(src_epsg)
    if dst_crs == src:
        return pt
    transform = QgsCoordinateTransform(src, dst_crs, QgsProject.instance())
    return transform.transform(pt)

def resolve_path(base_dir: Path, path_like: str) -> Path:
    """画像などの相対パスを絶対パスに解決"""
    p = Path(path_like or "").expanduser()
    return p if p.is_absolute() else base_dir / p

def get_attr_safe(feat, name, default=None):
    """QgsFeatureの属性を安全に取得"""
    try:
        return feat[name]
    except Exception:
        return default
    
# streetview
def make_streetview_url(lat: float, lon: float, heading: float = 0.0) -> str:
    """Google Street View のURLを生成（純関数）"""
    if lat is None or lon is None:
        raise ValueError("Invalid coordinates")

    return (
        f"https://www.google.com/maps/@?api=1&map_action=pano"
        f"&viewpoint={lat:.6f},{lon:.6f}"
        f"&heading={heading:.1f}&pitch=0&fov=90"
    )

def make_gmaps_search_url(lat: float, lon: float) -> str:
    """座標検索（フォールバック用）"""
    if lat is None or lon is None:
        raise ValueError("Invalid coordinates")
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f}%2C{lon:.6f}"
