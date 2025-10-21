import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List

from qgis.PyQt.QtCore import QSettings

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
