# layers.py
from typing import List, Optional
from qgis.core import QgsVectorLayer, QgsProject

from .utils import EditContext
from .symbology import apply_category_symbology, apply_click_count_labels
from .fields import FN, apply_schema

def _get_existing_layer(name: str) -> QgsVectorLayer:
    proj = QgsProject.instance()
    layers = proj.mapLayersByName(name)
    return layers[0] if layers else None

def ensure_click_layer(
    name: str,
    extra_fields: Optional[List[str]] = None,
    category_symbols: Optional[dict] = None,
) -> QgsVectorLayer:
    exist = _get_existing_layer(name)
    if exist:
        apply_schema(exist, extra_fields)
        apply_category_symbology(
            exist,
            field_name=FN.CATEGORY,
            category_symbols=category_symbols,
        )
        apply_click_count_labels(exist)
        update_same_point_counts(exist)
        return exist

    uri = (
        "Point?crs=epsg:4326"
        f"&field={FN.LAT}:double&field={FN.LON}:double&field={FN.JPG}:string"
        f"&field={FN.CATEGORY}:string&field=subcat:string"
    )
    lyr = QgsVectorLayer(uri, name, "memory")
    if not lyr.isValid():
        raise Exception("Failed to create click layer.")
    QgsProject.instance().addMapLayer(lyr)
    apply_schema(lyr, extra_fields)
    apply_category_symbology(
        lyr,
        field_name=FN.CATEGORY,
        category_symbols=category_symbols,
    )
    apply_click_count_labels(lyr)
    update_same_point_counts(lyr)
    return lyr

def ensure_count_field(layer):
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import QgsField

    if layer.fields().indexFromName("count_same") < 0:
        layer.dataProvider().addAttributes([QgsField("count_same", QVariant.Int)])
        layer.updateFields()

def update_same_point_counts(layer, tol=1e-7):
    if not layer or not layer.isValid():
        return

    ensure_count_field(layer)

    groups = {}
    feats = list(layer.getFeatures())

    for f in feats:
        try:
            p = f.geometry().asPoint()
            key = (round(p.x() / tol), round(p.y() / tol))
            groups.setdefault(key, []).append(f.id())
        except Exception:
            pass

    idx = layer.fields().indexFromName("count_same")
    if idx < 0:
        return

    with EditContext(layer):
        for ids in groups.values():
            n = len(ids)
            for fid in ids:
                layer.changeAttributeValue(fid, idx, n)

    layer.triggerRepaint()
