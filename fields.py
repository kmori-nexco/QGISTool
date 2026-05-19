# fields.py
from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsField, QgsVectorLayer
from typing import List, Optional, Tuple, Dict
from contextlib import contextmanager
from collections import OrderedDict
import csv

class FN:
    CATEGORY = "category"
    LAT = "lat"
    LON = "lon"
    JPG = "jpg"

DEFAULT_USER_ATTR_SPECS: List[Tuple[str, Optional[List[str]]]] = [
    ("traffic sign", ["stop", "yield", "speed limit", "do not enter"]),
    ("pole", ["utility", "light"]),
    ("fire hydrant", ["fire hydrant"]),
    ("unknown", ["unknown"]),
]

DEFAULT_MAIN_TO_SUBFIELD: Dict[str, str] = {
    "traffic sign": "traffic sign",
    "pole": "pole",
    "fire hydrant": "fire hydrant",
    "unknown": "unknown",
}


def load_category_master(csv_path: str):
    specs = OrderedDict()
    main_to_field = {}
    category_symbols = {}

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)

        required = {"category", "subcategory", "field_name"}
        missing = required - set(rdr.fieldnames or [])
        if missing:
            raise Exception(f"Category master missing headers: {', '.join(sorted(missing))}")

        for row in rdr:
            enabled = str(row.get("enabled", "1")).strip().lower()
            if enabled in ("0", "false", "no", "n"):
                continue

            cat = (row.get("category") or "").strip()
            sub = (row.get("subcategory") or "").strip()
            field = (row.get("field_name") or cat).strip()

            if not cat:
                continue

            cat_norm = normalize_category(cat)

            specs.setdefault(cat, [])
            if sub and sub not in specs[cat]:
                specs[cat].append(sub)

            main_to_field[cat_norm] = field

            try:
                symbol_id = int(str(row.get("symbol_id", "10")).strip() or "10")
            except Exception:
                symbol_id = 10

            category_symbols[cat_norm] = symbol_id

    return list(specs.items()), main_to_field, category_symbols


def build_category_runtime(csv_path: Optional[str] = None):
    if csv_path:
        attr_specs, main_to_field, category_symbols = load_category_master(csv_path)
    else:
        attr_specs = DEFAULT_USER_ATTR_SPECS
        main_to_field = {
            normalize_category(cat): field
            for cat, field in DEFAULT_MAIN_TO_SUBFIELD.items()
        }
        category_symbols = {
            "traffic sign": 1,
            "pole": 2,
            "fire hydrant": 3,
            "unknown": 10,
        }

    group_keep = {
        normalize_category(cat): [field]
        for cat, field in main_to_field.items()
    }

    other_candidates = list(dict.fromkeys(main_to_field.values()))

    return attr_specs, main_to_field, group_keep, other_candidates, category_symbols


def apply_schema(layer: QgsVectorLayer, extra_fields: Optional[List[str]] = None) -> None:
    need_specs = {
        FN.CATEGORY: (QVariant.String, 64),
        FN.LAT: (QVariant.Double, None),
        FN.LON: (QVariant.Double, None),
        FN.JPG: (QVariant.String, 255),
        "subcat": (QVariant.String, 64),
    }

    for name in extra_fields or []:
        if name:
            need_specs[name] = (QVariant.String, 128)

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


def normalize_category(raw: str) -> str:
    return (raw or "").strip().lower().replace("_", " ")


def clear_unrelated_category_attrs(
    layer: QgsVectorLayer,
    feature,
    category_norm: str,
    group_keep: Optional[Dict[str, List[str]]] = None,
    other_candidates: Optional[List[str]] = None,
) -> None:
    group_keep = group_keep or {}
    other_candidates = other_candidates or []

    keep = set(n.lower() for n in group_keep.get(category_norm, []))
    layer_field_names_lc = {f.name().lower() for f in layer.fields()}

    for cand in other_candidates:
        if cand.lower() in layer_field_names_lc:
            idx = layer.fields().indexFromName(cand)
            if idx >= 0 and cand.lower() not in keep:
                feature.setAttribute(idx, None)


@contextmanager
def edit(layer: QgsVectorLayer):
    started_here = False
    if layer and not layer.isEditable():
        layer.startEditing()
        started_here = True
    try:
        yield
        if started_here:
            layer.commitChanges()
    except Exception:
        if started_here:
            layer.rollBack()
        raise
