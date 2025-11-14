# symbology.py
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProperty, QgsSymbolLayer, QgsMarkerSymbol,
    QgsCategorizedSymbolRenderer, QgsRendererCategory,
    QgsFontMarkerSymbolLayer, QgsVectorLayer,
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


def apply_category_symbology(layer: QgsVectorLayer, field_name: str = FN.CATEGORY):
    if not layer or not layer.isValid() or field_name not in layer.fields().names():
        return

    style_defs = {
        "traffic sign": {"name": "square",
                         "color": "0,255,0,255", 
                         "size": "5.0", 
                         "label": "traffic sign"},
        "pole":         {"name": "star", 
                         "color": "255,255,0,255", 
                         "size": "5.5", 
                         "label": "pole"},
        "fire hydrant": {"name": "triangle", 
                         "color": "255,51,51,255", 
                         "size": "5.0", 
                         "label": "fire hydrant"},
    }

    categories = []
    for value, cfg in style_defs.items():
        sym = QgsMarkerSymbol.createSimple({
            "name": cfg["name"], "color": cfg["color"], "size": cfg["size"],
            "outline_color": "0,0,0,80", "outline_width": "0.3",
        })
        categories.append(QgsRendererCategory(value, sym, cfg["label"]))

    default_sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": "153,153,153,255", "size": "4.0"})
    renderer = QgsCategorizedSymbolRenderer(field_name, categories)
    renderer.setSourceSymbol(default_sym)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
