from typing import List
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsVectorLayer, QgsProject, QgsField, QgsGeometry, QgsPointXY, QgsFeature
)
from .utils import EditContext

def ensure_point_layer(name: str) -> QgsVectorLayer:
    uri = (
        "Point?crs=epsg:4326"
        "&field=kp:string&field=side:string&field=jpg:string"
        "&field=street:string"
        "&field=pic_front:string&field=pic_back:string"
        "&field=lat:double&field=lon:double&field=course:double"
        "&field=is_sel:int"
    )
    lyr = QgsVectorLayer(uri, name, "memory")
    if not lyr.isValid():
        raise Exception("ポイントレイヤの作成に失敗しました。")
    QgsProject.instance().addMapLayer(lyr)
    return lyr

def ensure_click_layer(name: str) -> QgsVectorLayer:
    uri = "Point?crs=epsg:4326&field=lat:double&field=lon:double&field=jpg:string"
    lyr = QgsVectorLayer(uri, name, "memory")
    if not lyr.isValid():
        raise Exception("クリック追加用レイヤの作成に失敗しました。")
    QgsProject.instance().addMapLayer(lyr)
    return lyr

def ensure_fields(lyr: QgsVectorLayer, keys: List[str]):
    names = set(lyr.fields().names())
    new_fields = []
    for k in keys:
        if k and k not in names:
            typ = QVariant.Int if k == "is_sel" else QVariant.String
            new_fields.append(QgsField(k, typ))
    if new_fields:
        with EditContext(lyr):
            lyr.dataProvider().addAttributes(new_fields)
            lyr.updateFields()

def plot_all_points(layer: QgsVectorLayer, rows: List, info_cb=None):
    """rows は utils.Row の配列"""
    prov = layer.dataProvider()
    idx = {n: layer.fields().indexFromName(n) for n in layer.fields().names()}
    new_feats = []
    with EditContext(layer):
        # 既存クリア
        layer.deleteFeatures([f.id() for f in layer.getFeatures()])

        for r in rows:
            # ---- KP 点（向きは不要なので course は設定しない）----
            if (r.lat_kp is not None) and (r.lon_kp is not None):
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_kp, r.lat_kp)))
                f.setAttribute(idx["kp"],        r.kp)
                f.setAttribute(idx["side"],      "kp")
                f.setAttribute(idx["jpg"],       "")
                f.setAttribute(idx["street"],    r.street or "")
                f.setAttribute(idx["pic_front"], r.front or "")
                f.setAttribute(idx["pic_back"],  r.back or "")
                f.setAttribute(idx["lat"],       r.lat_kp)
                f.setAttribute(idx["lon"],       r.lon_kp)
                f.setAttribute(idx["is_sel"],    0)
                new_feats.append(f)

            # ---- front ----
            if r.front and r.lat_front is not None and r.lon_front is not None:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_front, r.lat_front)))
                f.setAttribute(idx["kp"],        r.kp)
                f.setAttribute(idx["side"],      "front")
                f.setAttribute(idx["jpg"],       r.front)
                f.setAttribute(idx["street"],    r.street or "")
                f.setAttribute(idx["pic_front"], r.front)
                f.setAttribute(idx["pic_back"],  r.back or "")
                f.setAttribute(idx["lat"],       r.lat_front)
                f.setAttribute(idx["lon"],       r.lon_front)
                f.setAttribute(idx["is_sel"],    0)
                # ★ 向き（course）を反映
                if "course" in idx and getattr(r, "course_front", None) is not None:
                    f.setAttribute(idx["course"], float(r.course_front))
                new_feats.append(f)

            # ---- back ----
            if r.back and r.lat_back is not None and r.lon_back is not None:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_back, r.lat_back)))
                f.setAttribute(idx["kp"],        r.kp)
                f.setAttribute(idx["side"],      "back")
                f.setAttribute(idx["jpg"],       r.back)
                f.setAttribute(idx["street"],    r.street or "")
                f.setAttribute(idx["pic_front"], r.front or "")
                f.setAttribute(idx["pic_back"],  r.back)
                f.setAttribute(idx["lat"],       r.lat_back)
                f.setAttribute(idx["lon"],       r.lon_back)
                f.setAttribute(idx["is_sel"],    0)
                # ★ 向き（course）を反映
                if "course" in idx and getattr(r, "course_back", None) is not None:
                    f.setAttribute(idx["course"], float(r.course_back))
                new_feats.append(f)

        if new_feats:
            if not prov.addFeatures(new_feats):
                raise Exception("フィーチャの追加に失敗しました。")

    layer.removeSelection()
    layer.triggerRepaint()
    if info_cb:
        info_cb(len(new_feats))
