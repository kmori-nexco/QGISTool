# layers.py
from typing import List, Optional, Tuple
from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsVectorLayer, QgsProject, QgsField, 
                       QgsGeometry, QgsPointXY, QgsFeature,
                       QgsFeatureRequest,)

from .utils import EditContext
from .symbology import apply_category_symbology
from .fields import FN, apply_schema


# フィールド名ごとの標準型（必要最低限）
_FIELD_TYPE_MAP = {
    FN.LAT: QVariant.Double,
    FN.LON: QVariant.Double,
    FN.JPG: QVariant.String,
    FN.CATEGORY: QVariant.String,
    "subcat": QVariant.String,
    "kp": QVariant.String,
    "side": QVariant.String,
    "street": QVariant.String,
    "pic_front": QVariant.String,
    "pic_back": QVariant.String,
    "course": QVariant.Double,
    "is_sel": QVariant.Int,
}

def _get_existing_layer(name: str) -> QgsVectorLayer:
    proj = QgsProject.instance()
    layers = proj.mapLayersByName(name)
    return layers[0] if layers else None

def ensure_point_layer(name: str) -> QgsVectorLayer:
    exist = _get_existing_layer(name)
    if exist:
        return exist

    uri = (
        "Point?crs=epsg:4326"
        f"&field=kp:string&field=side:string&field={FN.JPG}:string"
        "&field=street:string"
        "&field=pic_front:string&field=pic_back:string"
        f"&field={FN.LAT}:double&field={FN.LON}:double&field=course:double"
        "&field=is_sel:int"
        f"&field={FN.CATEGORY}:string&field=subcat:string"
    )
    lyr = QgsVectorLayer(uri, name, "memory")
    if not lyr.isValid():
        raise Exception("Failed to create point layer.")
    QgsProject.instance().addMapLayer(lyr)
    apply_schema(lyr)  # 念のため不足分を補う
    return lyr

def ensure_click_layer(name: str) -> QgsVectorLayer:
    exist = _get_existing_layer(name)
    if exist:
        apply_category_symbology(exist, field_name=FN.CATEGORY)
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
    apply_schema(lyr)
    apply_category_symbology(lyr, field_name=FN.CATEGORY)
    return lyr

def ensure_fields(lyr: QgsVectorLayer, keys: List[str]):
    names = set(lyr.fields().names())
    new_fields = []
    for k in keys:
        if k and k not in names:
            typ = _FIELD_TYPE_MAP.get(k, QVariant.String)
            new_fields.append(QgsField(k, typ))
    if new_fields:
        with EditContext(lyr):
            lyr.dataProvider().addAttributes(new_fields)
            lyr.updateFields()

def plot_all_points(layer: QgsVectorLayer, rows: List, info_cb=None):
    required_fields = [
        "kp", "side", FN.JPG, "street",
        "pic_front", "pic_back",
        FN.LAT, FN.LON, "course",
        "is_sel",
        FN.CATEGORY, "subcat",
    ]
    ensure_fields(layer, required_fields)

    prov = layer.dataProvider()
    idx = {n: layer.fields().indexFromName(n) for n in layer.fields().names()}
    new_feats = []

    with EditContext(layer):
        layer.deleteFeatures([f.id() for f in layer.getFeatures()])

        for r in rows:
            # ---- KP ----
            if getattr(r, "lat_kp", None) is not None and getattr(r, "lon_kp", None) is not None:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_kp, r.lat_kp)))
                f.setAttribute(idx["kp"],        r.kp)
                f.setAttribute(idx["side"],      "kp")
                f.setAttribute(idx[FN.JPG],      "")
                f.setAttribute(idx["street"],    r.street or "")
                f.setAttribute(idx["pic_front"], r.front or "")
                f.setAttribute(idx["pic_back"],  r.back or "")
                f.setAttribute(idx[FN.LAT],      r.lat_kp)
                f.setAttribute(idx[FN.LON],      r.lon_kp)
                f.setAttribute(idx["is_sel"],    0)
                if getattr(r, "category", None) is not None and FN.CATEGORY in idx:
                    f.setAttribute(idx[FN.CATEGORY], r.category)
                if getattr(r, "subcat", None) is not None and "subcat" in idx:
                    f.setAttribute(idx["subcat"], r.subcat)
                new_feats.append(f)

            # ---- front ----
            if getattr(r, "front", None) and getattr(r, "lat_front", None) is not None and getattr(r, "lon_front", None) is not None:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_front, r.lat_front)))
                f.setAttribute(idx["kp"],        r.kp)
                f.setAttribute(idx["side"],      "front")
                f.setAttribute(idx[FN.JPG],      r.front)
                f.setAttribute(idx["street"],    r.street or "")
                f.setAttribute(idx["pic_front"], r.front)
                f.setAttribute(idx["pic_back"],  r.back or "")
                f.setAttribute(idx[FN.LAT],      r.lat_front)
                f.setAttribute(idx[FN.LON],      r.lon_front)
                f.setAttribute(idx["is_sel"],    0)
                cf = getattr(r, "course_front", None)
                if "course" in idx and cf is not None:
                    f.setAttribute(idx["course"], float(cf))
                if getattr(r, "category", None) is not None and FN.CATEGORY in idx:
                    f.setAttribute(idx[FN.CATEGORY], r.category)
                if getattr(r, "subcat", None) is not None and "subcat" in idx:
                    f.setAttribute(idx["subcat"], r.subcat)
                new_feats.append(f)

            # ---- back ----
            if getattr(r, "back", None) and getattr(r, "lat_back", None) is not None and getattr(r, "lon_back", None) is not None:
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(r.lon_back, r.lat_back)))
                f.setAttribute(idx["kp"],        r.kp)
                f.setAttribute(idx["side"],      "back")
                f.setAttribute(idx[FN.JPG],      r.back)
                f.setAttribute(idx["street"],    r.street or "")
                f.setAttribute(idx["pic_front"], r.front or "")
                f.setAttribute(idx["pic_back"],  r.back)
                f.setAttribute(idx[FN.LAT],      r.lat_back)
                f.setAttribute(idx[FN.LON],      r.lon_back)
                f.setAttribute(idx["is_sel"],    0)
                cb = getattr(r, "course_back", None)
                if "course" in idx and cb is not None:
                    f.setAttribute(idx["course"], float(cb))
                if getattr(r, "category", None) is not None and FN.CATEGORY in idx:
                    f.setAttribute(idx[FN.CATEGORY], r.category)
                if getattr(r, "subcat", None) is not None and "subcat" in idx:
                    f.setAttribute(idx["subcat"], r.subcat)
                new_feats.append(f)

        if new_feats:
            if not prov.addFeatures(new_feats):
                raise Exception("Failed to add features.")

    layer.removeSelection()
    layer.triggerRepaint()
    if info_cb:
        info_cb(len(new_feats))

class FieldCache:
    def __init__(self, layer):
        f = layer.fields()
        self.idx = {name: f.indexFromName(name) for name in f.names()}
        self.kp      = self.idx.get("kp", -1)
        self.side    = self.idx.get("side", -1)
        self.jpg     = self.idx.get(FN.JPG, -1)
        self.is_sel  = self.idx.get("is_sel", -1)
        self.is_sf   = self.idx.get("is_sel_front", -1)
        self.is_sb   = self.idx.get("is_sel_back", -1)

    def has(self, name: str) -> bool:
        return self.idx.get(name, -1) >= 0
    
def ensure_sel_fields(layer):
    keys = ["is_sel_front", "is_sel_back"]
    ensure_fields(layer, keys)

# front/back の強調フラグを書き込んでリペイント
def apply_front_back_selected(layer, front_feat: Optional[QgsFeature], back_feat: Optional[QgsFeature], fcache: Optional[FieldCache] = None):
    if layer is None:
        return
    fcache = fcache or FieldCache(layer)
    if fcache.is_sf < 0 and fcache.is_sb < 0:
        return

    fid_front = front_feat.id() if front_feat else None
    fid_back  = back_feat.id()  if back_feat  else None

    with EditContext(layer):
        for f in layer.getFeatures():
            if fcache.is_sf >= 0:
                want = 1 if (fid_front is not None and f.id() == fid_front) else 0
                cur  = int(f[fcache.is_sf] or 0)
                if cur != want:
                    layer.changeAttributeValue(f.id(), fcache.is_sf, want)
            if fcache.is_sb >= 0:
                want = 1 if (fid_back is not None and f.id() == fid_back) else 0
                cur  = int(f[fcache.is_sb] or 0)
                if cur != want:
                    layer.changeAttributeValue(f.id(), fcache.is_sb, want)
    layer.triggerRepaint()

# KP（side=kp）行の is_sel を 0/1 に更新
def select_kp(layer, kp_value: str, fcache: Optional[FieldCache] = None):
    if not layer or not kp_value:
        return
    fcache = fcache or FieldCache(layer)
    if min(fcache.is_sel, fcache.side, fcache.kp) < 0:
        return

    key = str(kp_value).strip().lower()
    with EditContext(layer):
        for f in layer.getFeatures():
            try:
                if str(f[fcache.side]).strip().lower() == "kp":
                    want = 1 if str(f[fcache.kp]).strip().lower() == key else 0
                    cur  = int(f[fcache.is_sel] or 0)
                    if cur != want:
                        layer.changeAttributeValue(f.id(), fcache.is_sel, want)
            except Exception:
                pass
    layer.triggerRepaint()

# 画像名 or 座標（±tol）で候補を検索（expected_side: "front"/"back"/None）
# まず画像名があればそれを優先。なければ座標で探す。
def find_feature_by_pic_or_coord(
    layer,
    pic: Optional[str],
    lat: Optional[float],
    lon: Optional[float],
    *,
    expected_side: Optional[str] = None,
    tol: float = 1e-7
):
    if not layer:
        return None
    fcache = FieldCache(layer)
    exp = (expected_side or "").strip().lower()

    # 画像名で検索
    key = (pic or "").strip().lower()
    if key and fcache.jpg >= 0:
        req = QgsFeatureRequest()
        # 線形走査（まずは等価移設）。後で属性インデックス or selectByExpression に差し替え可
        for f in layer.getFeatures(req):
            try:
                if fcache.side >= 0 and exp and str(f[fcache.side]).strip().lower() != exp:
                    continue
                jpg = (str(f[fcache.jpg]) or "").strip().lower()
                if jpg == key:
                    return f
            except Exception:
                pass

    # 座標で検索
    if lat is not None and lon is not None:
        # 後で SpatialIndex に置き換え可能な形（まずは全走査）
        for f in layer.getFeatures():
            try:
                if fcache.side >= 0 and exp and str(f[fcache.side]).strip().lower() != exp:
                    continue
                pt = f.geometry().asPoint()
                if abs(pt.x() - lon) <= tol and abs(pt.y() - lat) <= tol:
                    return f
            except Exception:
                pass

    return None
