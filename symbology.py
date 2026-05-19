# symbology.py
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProperty, QgsSymbolLayer, QgsMarkerSymbol, QgsCategorizedSymbolRenderer,
    QgsRendererCategory, QgsFontMarkerSymbolLayer, QgsVectorLayer,
    QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling,
)
from .fields import FN


def apply_plane_symbology(
    layer: QgsVectorLayer,
    size: float = 14.0,
    angle_offset: float = 0.0,
):
    """side=front/back/kp で分類。front/back は✈フォント、kpはダイヤ"""
    if not layer or not layer.isValid():
        return

    def _make_symbol(color: QColor, extra_angle: float = 0.0, side_name: str = "") -> QgsMarkerSymbol:
        sym = QgsMarkerSymbol()

        # front/back の色（選択時に濃色）
        if side_name == "front":
            sel_expr = (
                "case when coalesce(\"is_sel_front\",0)=1 "
                "then '0,0,255,255' else '0,90,200,120' end"
            )
        elif side_name == "back":
            sel_expr = (
                "case when coalesce(\"is_sel_back\",0)=1 "
                "then '255,0,0,255' else '255,150,120,120' end"
            )
        else:
            sel_expr = None

        fm = QgsFontMarkerSymbolLayer()
        fm.setFontFamily("Arial")
        fm.setCharacter("✈")
        fm.setColor(color)
        fm.setSize(size)
        sym.changeSymbolLayer(0, fm)

        if side_name == "front":
            heading_field = "course_front"
        elif side_name == "back":
            heading_field = "course_back"
        else:
            heading_field = "course_front"

        angle_expr = (
            f"coalesce(\"{heading_field}\", 0)"
            f"+ ({ -90 + float(angle_offset) + float(extra_angle)})"
        )
        fm.setDataDefinedProperty(
            QgsSymbolLayer.PropertyAngle,
            QgsProperty.fromExpression(angle_expr),
        )

        # 選択時の色切替（塗り/線 両方に同じ式を設定）
        if sel_expr:
            for prop in (
                getattr(QgsSymbolLayer, "PropertyFillColor", None),
                getattr(QgsSymbolLayer, "PropertyStrokeColor", None),
            ):
                if prop is not None:
                    fm.setDataDefinedProperty(prop, QgsProperty.fromExpression(sel_expr))

        return sym

    def _make_kp_symbol() -> QgsMarkerSymbol:
        sym = QgsMarkerSymbol.createSimple(
            {"name": "diamond", "size": "5.0",
             "outline_color": "0,0,0,200", "outline_width": "0.4",
             "color": "180,0,255,220"}
        )
        sym.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.PropertySize,
            QgsProperty.fromExpression("case when coalesce(\"is_sel\",0)=1 then 3 else 0 end"),
        )
        return sym

    cats = [
        QgsRendererCategory("front", _make_symbol(QColor(0, 120, 255), side_name="front"), "front"),
        QgsRendererCategory("back",  _make_symbol(QColor(255, 80, 0), extra_angle=180, side_name="back"), "back"),
        QgsRendererCategory("kp",    _make_kp_symbol(), "kp"),
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("side", cats))
    layer.triggerRepaint()

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
