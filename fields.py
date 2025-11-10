#fields.py
from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsField, QgsVectorLayer
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from contextlib import contextmanager

# 1) フィールド名の集中管理
class FN:
    ID          = "id"
    CREATED_AT  = "created_at"
    USER        = "user"
    CATEGORY    = "category"
    NOTES       = "notes"
    LAT         = "lat"
    LON         = "lon"
    JPG         = "jpg"
    TRAFFIC_SIGN = "traffic sign"
    POLE         = "pole"
    FIREHYDRANT  = "fire hydrant"

USER_ATTR_SPECS: List[Tuple[str, Optional[List[str]]]] = [
    ("traffic sign", [
        "stop", "yield", "speed limit", "do not enter",
        "one way", "pedestrian crossing", "school zone",
        "regulatory signs", "warning signs", "guide signs", "non-standard signs",
    ]),
    ("pole", ["utility", "light"]),
    ("fire hydrant", ["fire hydrant"]),
]

MAIN_TO_SUBFIELD: Dict[str, str] = {
    "traffic sign": FN.TRAFFIC_SIGN,
    "pole": FN.POLE,
    "fire hydrant": FN.FIREHYDRANT,
}

def apply_schema(layer: QgsVectorLayer) -> None:
    need_specs = {
        FN.CATEGORY: (QVariant.String, 32),
        FN.LAT: (QVariant.Double, None),
        FN.LON: (QVariant.Double, None),
        FN.JPG: (QVariant.String, 255),
        FN.TRAFFIC_SIGN: (QVariant.String, 64),
        FN.POLE: (QVariant.String, 64),
        FN.FIREHYDRANT: (QVariant.String, 64),
    }
    existing = set(layer.fields().names())
    adds = []
    for name, (qv, length) in need_specs.items():
        if name not in existing:
            f = QgsField(name, qv)            
            if length:
                f.setLength(length)
            adds.append(f)
    if adds:
        with edit(layer):
            layer.dataProvider().addAttributes(adds)
            layer.updateFields()

# 3) カテゴリ名の正規化と、カテゴリ外フィールドのNull化
#    “traffic sign”/”fire hydrant”など表記ゆれを吸収
CANON_CATEGORIES = {
    "traffic sign": "traffic sign",
    "trafficsign": "traffic sign",
    "sign": "traffic sign",
    "pole": "pole",
    "fire hydrant": "fire hydrant",
    "firehydrant": "fire hydrant",
    "hydrant": "fire hydrant",
}

# このカテゴリで残すべきフィールド
GROUP_KEEP: Dict[str, List[str]] = {
    "traffic sign": [FN.TRAFFIC_SIGN],
    "pole": [FN.POLE],
    "fire hydrant": [FN.FIREHYDRANT],
}

# レイヤ上の候補（存在確認してから処理）
OTHER_CANDIDATES: List[str] = [FN.TRAFFIC_SIGN, FN.POLE, FN.FIREHYDRANT]

def normalize_category(raw: str) -> str:
    key = (raw or "").strip().lower().replace("_", " ")
    return CANON_CATEGORIES.get(key, key)

def clear_unrelated_category_attrs(layer: QgsVectorLayer, feature, category_norm: str) -> None:
    """カテゴリに関係ない候補フィールドへ None を入れる"""
    keep = set(n.lower() for n in GROUP_KEEP.get(category_norm, []))
    layer_field_names_lc = {f.name().lower() for f in layer.fields()}
    for cand in OTHER_CANDIDATES:
        if cand.lower() in layer_field_names_lc:
            idx = layer.fields().indexFromName(cand)
            if idx >= 0 and cand.lower() not in keep:
                feature.setAttribute(idx, None)

@contextmanager
def edit(layer: QgsVectorLayer):
    layer.startEditing()
    try:
        yield
        layer.commitChanges()
    except Exception:
        layer.rollBack()
        raise

def get_user_attr_specs() -> List[Tuple[str, Optional[List[str]]]]:
    return USER_ATTR_SPECS