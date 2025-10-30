from pathlib import Path
from typing import Optional
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProperty, QgsSymbolLayer, QgsMarkerSymbol,
    QgsCategorizedSymbolRenderer, QgsRendererCategory,
    QgsSvgMarkerSymbolLayer, QgsFontMarkerSymbolLayer, QgsApplication, QgsUnitTypes
)

def _find_builtin_plane_svg() -> Optional[str]:
    candidates = [
        "transport/transport_airport.svg", "transport/airplane.svg", "transport/airport.svg", "transport/plane.svg",
        "symbols/transport/transport_airport.svg", "symbols/transport/airplane.svg",
    ]
    try:
        for base in QgsApplication.svgPaths():
            for name in candidates:
                p = Path(base) / name
                if p.exists():
                    return str(p)
    except Exception:
        pass
    return None

def apply_plane_symbology(layer, size: float = 14.0, angle_offset: float = 0.0,
                          prefer_font=True, plane_svg_path: Optional[str]=None):
    plane_svg = None
    if not prefer_font:
        if plane_svg_path and Path(plane_svg_path).exists():
            plane_svg = plane_svg_path
        else:
            plane_svg = _find_builtin_plane_svg()

    def _make_symbol(color: QColor, extra_angle: float = 0.0):
        sym = QgsMarkerSymbol()
        if plane_svg:
            svg = QgsSvgMarkerSymbolLayer(plane_svg, size)
            sym.changeSymbolLayer(0, svg)
            lyr = sym.symbolLayer(0)
        else:
            fm = QgsFontMarkerSymbolLayer()
            fm.setFontFamily("Arial")
            fm.setCharacter("✈")
            fm.setSize(size)
            sym.changeSymbolLayer(0, fm)
            lyr = sym.symbolLayer(0)
    
        expr_angle = f"case when \"course\" is null then 90 else 90 - \"course\" end + ({float(angle_offset) + float(extra_angle)})"
        lyr.setDataDefinedProperty(QgsSymbolLayer.PropertyAngle, QgsProperty.fromExpression(expr_angle))
    
        color_expr = (
            "CASE WHEN \"is_show\"=1 "
            f"THEN color_rgb({color.red()},{color.green()},{color.blue()}) "
            "ELSE color_rgb(150,150,150) END"
        )
        lyr.setDataDefinedProperty(QgsSymbolLayer.PropertyFillColor, QgsProperty.fromExpression(color_expr))
    
        lyr.setDataDefinedProperty(
            QgsSymbolLayer.PropertySize,
            QgsProperty.fromExpression(f"{float(size)}")
        )
    
        lyr.setSizeUnit(QgsUnitTypes.RenderMillimeters)
        return sym

    # kpのシンボル設定
    def _make_kp_symbol():
        sym = QgsMarkerSymbol.createSimple({
            "name": "diamond",
            "size": "5",
            "outline_color": "0,0,0,200",
            "outline_width": "0.4",
            "color": "180,0,255,220"
        })
        lyr = sym.symbolLayer(0)
        lyr.setDataDefinedProperty(
            QgsSymbolLayer.PropertySize,
            QgsProperty.fromExpression("case when \"is_sel\"=1 then 5 else 0 end")
        )
        try:
            lyr.setSizeUnit(QgsUnitTypes.RenderMillimeters)
        except Exception:
            pass
        return sym

    cats = [
        QgsRendererCategory("front", _make_symbol(QColor(120,180,255)), "front"),  # 薄い青
        QgsRendererCategory("back",  _make_symbol(QColor(255,180,120), extra_angle=180), "back"),  # 薄いオレンジ
        QgsRendererCategory("kp",    _make_kp_symbol(), "kp"),
    ]
    
    renderer = QgsCategorizedSymbolRenderer("side", cats)
    layer.setRenderer(renderer)
    layer.triggerRepaint()

