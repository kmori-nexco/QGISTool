#viewer.py
import re
from pathlib import Path
from typing import Optional, Dict, List

from qgis.PyQt.QtCore import Qt, QStandardPaths
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox, QDialog
from qgis.utils import iface

from .utils import settings, normalize_header, SKEY_ROOT
from .fields import FN, build_category_runtime

from . import dialogs
from . import layers as lyrmod
from . import maptools
from . import ui as ui_mod
from . import io as io_mod


def _qt_enum(container, scoped_name: str, legacy_name: str = None, default=None):
    obj = container
    try:
        for part in scoped_name.split("."):
            obj = getattr(obj, part)
        return obj
    except AttributeError:
        pass

    if legacy_name is not None:
        try:
            return getattr(container, legacy_name)
        except AttributeError:
            pass

    if default is not None:
        return default

    raise AttributeError(
        f"Could not resolve Qt enum: {container}.{scoped_name}"
        + (f" or legacy {legacy_name}" if legacy_name else "")
    )


class QGISToolController:
    CLICK_LAYER_NAME = "PhotoClicks"
    SKEY_LAST_EXPORT_CLICKS = "QGISTool/last_export_clicks_csv"
    SKEY_CAT_MASTER = f"{SKEY_ROOT}category_master_csv"

    def __init__(self):
        self.click_layer = None
        self.COORD_TOL = 1e-7
        self._prev_map_tool = None
        self._click_tool = None
        self._edit_tool = None
        self._streetview_tool = None
        self.attr_specs, self.main_to_field, self.group_keep, self.other_candidates, self.category_symbols = build_category_runtime()

        # --- PyQt5 / PyQt6 互換ヘルパ ---------------------------------
        self._APP_MODAL = _qt_enum(
            Qt, "WindowModality.ApplicationModal", "ApplicationModal"
        )

        self._DIALOG_ACCEPTED = _qt_enum(
            QDialog, "DialogCode.Accepted", "Accepted"
        )

        def _exec_dialog(dlg):
            try:
                return dlg.exec()
            except AttributeError:
                return dlg.exec_()
        self._exec_dialog = _exec_dialog

        self._DOCS_LOC = _qt_enum(
            QStandardPaths,
            "StandardLocation.DocumentsLocation",
            "DocumentsLocation"
        )
        self._TEMP_LOC = _qt_enum(
            QStandardPaths,
            "StandardLocation.TempLocation",
            "TempLocation"
        )
        self._writable_location = QStandardPaths.writableLocation
        # -------------------------------------------------------------

        self._build_ui()

    def _build_ui(self):
        self.dock = ui_mod.create_dock()
        self.add_btn = self.dock.add_btn
        self.edit_btn = self.dock.edit_btn
        self.streetview_btn = self.dock.streetview_btn

        self.dock.categoryMasterRequested.connect(self.select_category_master)
        self.dock.streetViewModeToggled.connect(self._toggle_streetview_mode)
        self.dock.addModeToggled.connect(self._toggle_add_mode)
        self.dock.editModeToggled.connect(self._toggle_edit_mode)
        self.dock.importClicksRequested.connect(self._import_clicks_csv)
        self.dock.exportClicksRequested.connect(self._export_clicks_csv)

    def select_category_master(self):
        last_cat = settings.value(self.SKEY_CAT_MASTER, '', type=str) or ''

        cat_csv, _ = QFileDialog.getOpenFileName(
            iface.mainWindow(),
            "Select Category Master CSV",
            last_cat,
            "CSV (*.csv)"
        )
        if not cat_csv:
            return

        try:
            (
                self.attr_specs,
                self.main_to_field,
                self.group_keep,
                self.other_candidates,
                self.category_symbols,
            ) = build_category_runtime(cat_csv)

            settings.setValue(self.SKEY_CAT_MASTER, cat_csv)

            if self.click_layer and self.click_layer.isValid():
                self.click_layer = lyrmod.ensure_click_layer(
                    self.CLICK_LAYER_NAME,
                    getattr(self, "other_candidates", []),
                    getattr(self, "category_symbols", {}),
                )

            QMessageBox.information(
                iface.mainWindow(),
                "Category Master",
                f"Loaded category master:\n{cat_csv}"
            )

        except Exception as e:
            QMessageBox.critical(
                iface.mainWindow(),
                "Category Master Error",
                f"Failed to load category master\n{e}"
            )

    def _ensure_click_layer_or_msg(self, title: str):
        try:
            lyr = lyrmod.ensure_click_layer(
                self.CLICK_LAYER_NAME,
                getattr(self, "other_candidates", []),
                getattr(self, "category_symbols", {}),
            )
            self.click_layer = lyr
            return lyr
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), title, f"Failed to create click layer\n{e}")
            return None

    def force_disable_map_tools(self):
        canvas = iface.mapCanvas()
        for attr in ("_click_tool", "_edit_tool", "_streetview_tool"):
            tool = getattr(self, attr, None)
            if tool:
                maptools.disable_current_tool(canvas, getattr(self, "_prev_map_tool", None))
                setattr(self, attr, None)

    def _prompt_attributes(self) -> Optional[List[Dict[str, str]]]:
        last: Dict[str, str] = {}
        dlg = dialogs.AttrDialog(iface.mainWindow(), self.attr_specs, last)
        res = self._exec_dialog(dlg)
        if res != self._DIALOG_ACCEPTED:
            return None

        selected = dlg.values()
        if not selected:
            QMessageBox.information(
                iface.mainWindow(),
                "Select Attributes",
                "No category selected."
            )
            return None

        selected_lc = {(k or "").strip().lower(): (v or "") for k, v in selected.items()}
        chosen_main = [k for k, v in selected_lc.items() if v]

        results: List[Dict[str, str]] = []

        def _parse_sub_vals(val: str) -> List[str]:
            if not val:
                return []
            out: List[str] = []
            for tok in [t.strip().lower() for t in val.split(",") if t.strip()]:
                m = re.match(r"^(.*?)(?:\s*=\s*(\d+))?$", tok)
                if not m:
                    continue
                label = (m.group(1) or "").strip()
                if not label:
                    continue
                n = int(m.group(2)) if m.group(2) else 1
                n = max(1, min(n, 999))
                out.extend([label] * n)
            return out

        total_items = sum(len(_parse_sub_vals(selected_lc.get(main, ""))) for main in chosen_main)
        will_be_multi = total_items >= 2

        if chosen_main:
            for main in chosen_main:
                sub_vals = _parse_sub_vals(selected_lc.get(main, ""))

                if sub_vals:
                    for sub in sub_vals:
                        d: Dict[str, str] = {FN.CATEGORY: main}
                        for k, v in selected.items():
                            if not v:
                                continue
                            key = normalize_header(k)
                            low = key.lower()
                            if low in ("category", "categories", "カテゴリ", "カテゴリー"):
                                continue
                            if k.lower() == main:
                                key = self.main_to_field.get(main, self.main_to_field.get(k, key))
                                d[key] = sub
                                continue
                            if key in ("lat", "lon", "jpg"):
                                key = f"user_{key}"
                            d[key] = v
                        if will_be_multi:
                            d["subcat"] = "combined"
                        results.append(d)
                else:
                    d: Dict[str, str] = {FN.CATEGORY: main}
                    for k, v in selected.items():
                        if not v:
                            continue
                        key = normalize_header(k)
                        if key in ("lat", "lon", "jpg"):
                            key = f"user_{key}"
                        d[key] = v
                    if will_be_multi:
                        d["subcat"] = "combined"
                    results.append(d)
        else:
            d: Dict[str, str] = {}
            for k, v in selected.items():
                if not v:
                    continue
                key = normalize_header(k)
                if key in ("lat", "lon", "jpg"):
                    key = f"user_{key}"
                d[key] = v
            if d:
                if "," in (selected.get("category", "") or ""):
                    d["subcat"] = "combined"
                results.append(d)

        return results or None

    def _toggle_add_mode(self):
        lyr = self._ensure_click_layer_or_msg("PhotoClicks")
        if not lyr:
            return

        maptools.toggle_tool_mode(
            self, iface.mapCanvas(), lyr,
            "_click_tool", "_prev_map_tool",
            maptools.AddPointTool,
            "● Add Click mode (ON)", "● Add Click mode", self.add_btn,
            conflict=[
                ("_edit_tool", "_prev_map_tool", "✎ Edit Click mode", "edit_btn"),
                ("_streetview_tool", "_prev_map_tool", "🌐 Street View Mode", "streetview_btn"),
            ],
        )

    def _toggle_edit_mode(self):
        lyr = self._ensure_click_layer_or_msg("PhotoClicks")
        if not lyr:
            return

        maptools.toggle_tool_mode(
            self, iface.mapCanvas(), lyr,
            "_edit_tool", "_prev_map_tool",
            maptools.EditPointTool,
            "✎ Edit Click mode (ON)", "✎ Edit Click mode", self.edit_btn,
            conflict=[
                ("_click_tool", "_prev_map_tool", "● Add Click mode", "add_btn"),
                ("_streetview_tool", "_prev_map_tool", "🌐 Street View Mode", "streetview_btn"),
            ],
        )

    def _toggle_streetview_mode(self, checked: bool):
        maptools.toggle_tool_mode(
            self, iface.mapCanvas(), None,
            "_streetview_tool", "_prev_map_tool",
            maptools.StreetViewTool,
            "🌐 Street View Mode (ON)", "🌐 Street View Mode", self.streetview_btn,
            conflict=[
                ("_click_tool", "_prev_map_tool", "● Add Click mode", "add_btn"),
                ("_edit_tool", "_prev_map_tool", "✎ Edit Click mode", "edit_btn"),
            ],
        )

    def _export_clicks_csv(self):
        lyr = self._ensure_click_layer_or_msg("Export CSV(Clicks)")
        if not lyr:
            return

        last_path = settings.value(self.SKEY_LAST_EXPORT_CLICKS, "", type=str)
        if not last_path:
            base_dir = self._writable_location(self._DOCS_LOC) or self._writable_location(self._TEMP_LOC)
            last_path = str(Path(base_dir) / "roadside_asset_clicks.csv")

        out_csv, _ = QFileDialog.getSaveFileName(
            iface.mainWindow(),
            "Save Clicked Points as CSV",
            last_path,
            "CSV (*.csv)"
        )
        if not out_csv:
            return

        sel_only = False
        if lyr.selectedFeatureCount() > 0:
            reply = QMessageBox.question(
                iface.mainWindow(),
                "Export CSV (Clicks)",
                "Export only selected features?\n Choose 'No' to export all.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            sel_only = (reply == QMessageBox.Yes)

        try:
            out_csv = io_mod.export_clicks_csv(lyr, out_csv, sel_only)
            settings.setValue(self.SKEY_LAST_EXPORT_CLICKS, out_csv)
            QMessageBox.information(iface.mainWindow(), "Export CSV (Clicks)", f"Saved:\n{out_csv}")
        except Exception as e:
            QMessageBox.critical(
                iface.mainWindow(),
                "Save CSV Error (Clicks)",
                f"Failed to save CSV\n{e}\n\n"
                "Try saving to a writable folder (e.g., Documents) or closing apps that may have the file open (e.g., Excel)."
            )

    def _import_clicks_csv(self):
        lyr = self._ensure_click_layer_or_msg("PhotoClicks")
        if not lyr:
            return

        last_path = settings.value(self.SKEY_LAST_EXPORT_CLICKS, "", type=str)
        csv_file, _ = QFileDialog.getOpenFileName(
            iface.mainWindow(),
            "Select Click Data CSV (lat,lon,category and attributes)",
            last_path or "",
            "CSV (*.csv);;All Files (*)"
        )
        if not csv_file:
            return

        from qgis.PyQt.QtWidgets import QProgressDialog
        prog = QProgressDialog("Loading Clicks CSV…", "Cancel", 0, 0, iface.mainWindow())
        prog.setWindowModality(self._APP_MODAL)
        prog.setMinimumDuration(400)

        def _tick(i):
            prog.setLabelText(f"Loading {i:,} rows …")
            prog.setValue(0)
            if prog.wasCanceled():
                raise Exception("Operation was canceled by the user")

        try:
            added, skipped = io_mod.import_clicks_csv(
                lyr, csv_file, dst_crs=lyr.crs(), clear=True, on_progress=_tick
            )
            lyr.triggerRepaint()
            ext = lyr.extent()
            if ext and not ext.isEmpty():
                iface.mapCanvas().setExtent(ext)
                iface.mapCanvas().refresh()

            msg = f"Import Completed: added {added} / skipped {skipped}."
            QMessageBox.information(iface.mainWindow(), "Import Clicks CSV", msg)
        except Exception as e:
            QMessageBox.critical(iface.mainWindow(), "Import Clicks CSV", f"Failed to import\n{e}")
        finally:
            try:
                prog.close()
            except Exception:
                pass
        lyrmod.update_same_point_counts(lyr)
