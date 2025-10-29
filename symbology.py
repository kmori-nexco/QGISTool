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
            try: svg.setColor(color)
            except Exception: pass
            try: svg.setFillColor(color)
            except Exception: pass
            try:
                svg.setOutlineColor(QColor(0,0,0,180)); svg.setOutlineWidth(0.3)
            except Exception: pass
            sym.changeSymbolLayer(0, svg)
            lyr = sym.symbolLayer(0)
        else:
            fm = QgsFontMarkerSymbolLayer()
            fm.setFontFamily("Arial"); fm.setCharacter("✈"); fm.setColor(color); fm.setSize(size)
            sym.changeSymbolLayer(0, fm)
            lyr = sym.symbolLayer(0)

        # 回転：course が無いときは 90（北向き）を基準に、QGISの角度系に合わせて 90 - course
        expr_angle = f"case when \"course\" is null then 90 else 90 - \"course\" end + ({float(angle_offset) + float(extra_angle)})"
        try:
            lyr.setDataDefinedProperty(QgsSymbolLayer.PropertyAngle, QgsProperty.fromExpression(expr_angle))
        except Exception:
            sym.setDataDefinedAngle(QgsProperty.fromExpression(expr_angle))

        try:
            lyr.setDataDefinedProperty(
                QgsSymbolLayer.PropertySize,
                QgsProperty.fromExpression(f"case when \"is_show\"=1 then {float(size)} else 0 end")
            )
        except Exception:
            pass

        try:
            lyr.setSizeUnit(QgsUnitTypes.RenderMillimeters)
        except Exception:
            pass

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
        QgsRendererCategory("front", _make_symbol(QColor(0,120,255)), "front"),
        QgsRendererCategory("back",  _make_symbol(QColor(255,80,0), extra_angle=180), "back"),
        QgsRendererCategory("kp",    _make_kp_symbol(), "kp"),
    ]
    renderer = QgsCategorizedSymbolRenderer("side", cats)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
                              
