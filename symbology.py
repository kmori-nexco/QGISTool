# symbology.py
from qgis.core import (
    QgsMarkerSymbol, QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsVectorLayer,
    QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling,
)
from .fields import FN


SYMBOL_PRESETS = {
    1: {"name": "square",   "color": "0,255,0,255",     "size": "5.0"},
    2: {"name": "star",     "color": "255,255,0,255",   "size": "5.5"},
    3: {"name": "triangle", "color": "255,51,51,255",   "size": "5.0"},
    4: {"name": "circle",   "color": "0,120,255,255",   "size": "5.0"},
    5: {"name": "diamond",  "color": "255,0,255,255",   "size": "5.0"},
    6: {"name": "cross",    "color": "0,200,200,255",   "size": "5.0"},
    7: {"name": "x",        "color": "255,150,0,255",   "size": "5.0"},
    8: {"name": "pentagon", "color": "120,80,255,255",  "size": "5.0"},
    9: {"name": "hexagon",  "color": "0,180,90,255",    "size": "5.0"},
    10: {"name": "circle",  "color": "128,128,128,255", "size": "4.5"},
}

def _make_marker_symbol(symbol_id: int) -> QgsMarkerSymbol:
    cfg = SYMBOL_PRESETS.get(int(symbol_id or 10), SYMBOL_PRESETS[10])

    return QgsMarkerSymbol.createSimple({
        "name": cfg["name"],
        "color": cfg["color"],
        "size": cfg["size"],
        "outline_color": "0,0,0,80",
        "outline_width": "0.3",
    })


def apply_category_symbology(
    layer: QgsVectorLayer,
    field_name: str = FN.CATEGORY,
    category_symbols=None,
):
    if not layer or not layer.isValid() or field_name not in layer.fields().names():
        return

    category_symbols = category_symbols or {
        "traffic sign": 1,
        "pole": 2,
        "fire hydrant": 3,
        "unknown": 10,
    }

    categories = []
    for category, symbol_id in category_symbols.items():
        sym = _make_marker_symbol(symbol_id)
        categories.append(QgsRendererCategory(category, sym, category))

    default_sym = _make_marker_symbol(10)

    renderer = QgsCategorizedSymbolRenderer(field_name, categories)
    renderer.setSourceSymbol(default_sym)

    layer.setRenderer(renderer)
    layer.triggerRepaint()
    
def apply_click_count_labels(layer):
    if not layer or not layer.isValid():
        return

    settings = QgsPalLayerSettings()
    settings.fieldName = 'CASE WHEN "count_same" > 1 THEN to_string("count_same") ELSE NULL END'
    settings.isExpression = True
    settings.enabled = True

    fmt = QgsTextFormat()
    settings.setFormat(fmt)

    layer.setLabelsEnabled(True)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.triggerRepaint()
