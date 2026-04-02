# dialogs.py
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox,
    QCheckBox, QLineEdit, QWidget, QSpinBox,
    QHBoxLayout, QMessageBox,
    QComboBox, QPushButton, QInputDialog
)
import re
import json
from typing import Dict, List, Optional, Tuple


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


_DBB_OK = _qt_enum(QDialogButtonBox, "StandardButton.Ok", "Ok")
_DBB_CANCEL = _qt_enum(QDialogButtonBox, "StandardButton.Cancel", "Cancel")

_MSG_YES = _qt_enum(QMessageBox, "StandardButton.Yes", "Yes")
_MSG_NO = _qt_enum(QMessageBox, "StandardButton.No", "No")


class AttrDialog(QDialog):
    _MV_RE = re.compile(r"^(.*?)(?:\s*=\s*(\d+))?$")

    def __init__(self, parent, attrs_spec: List[Tuple[str, Optional[List[str]]]], last_values: Dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Select attributes")
        self.rows = []

        lay = QVBoxLayout(self)

        self._settings = QSettings()
        self._preset_key = "PhotoClicks/AttrDialogPresets"

        preset_row = QWidget()
        preset_lay = QHBoxLayout(preset_row)
        preset_lay.setContentsMargins(0, 0, 0, 0)

        self.preset_combo = QComboBox()
        self.btn_apply_preset = QPushButton("Apply")
        self.btn_save_preset = QPushButton("Save as…")
        self.btn_delete_preset = QPushButton("Delete")

        preset_lay.addWidget(self.preset_combo, 1)
        preset_lay.addWidget(self.btn_apply_preset)
        preset_lay.addWidget(self.btn_save_preset)
        preset_lay.addWidget(self.btn_delete_preset)

        lay.addWidget(preset_row)

        self._presets = self._load_presets()
        self._refresh_preset_combo()

        self.btn_apply_preset.clicked.connect(self._on_apply_preset)
        self.btn_save_preset.clicked.connect(self._on_save_preset)
        self.btn_delete_preset.clicked.connect(self._on_delete_preset)

        form = QFormLayout()

        for name, options in attrs_spec:
            parent_chk = QCheckBox(name)

            if options:
                sub_container = QWidget()
                sub_layout = QVBoxLayout(sub_container)
                sub_layout.setContentsMargins(0, 0, 0, 0)

                sub_items = []
                for opt in options:
                    roww = QWidget()
                    rowl = QHBoxLayout(roww)
                    rowl.setContentsMargins(0, 0, 0, 0)

                    c = QCheckBox(opt)
                    s = QSpinBox()
                    s.setRange(1, 999)
                    s.setValue(1)
                    c.setEnabled(False)
                    s.setEnabled(False)

                    c.toggled.connect(s.setEnabled)

                    rowl.addWidget(c)
                    rowl.addStretch(1)
                    rowl.addWidget(s)
                    sub_layout.addWidget(roww)
                    sub_items.append((c, s))

                last_val = (last_values.get(name, "") or "").strip()
                if last_val:
                    wants = self._parse_multivalue(last_val)
                    any_checked = False
                    for c, s in sub_items:
                        if c.text() in wants:
                            c.setChecked(True)
                            s.setValue(wants[c.text()])
                            any_checked = True
                    if any_checked:
                        parent_chk.setChecked(True)

                def toggle_children(on: bool, items=sub_items):
                    for ch, sp in items:
                        ch.setEnabled(on)
                        sp.setEnabled(on and ch.isChecked())

                parent_chk.toggled.connect(toggle_children)

                editor = sub_items
                form.addRow(parent_chk, sub_container)

            else:
                editor = QLineEdit()
                editor.setText(last_values.get(name, ""))
                editor.setEnabled(False)
                parent_chk.toggled.connect(editor.setEnabled)
                form.addRow(parent_chk, editor)

            self.rows.append((name, parent_chk, editor))

        lay.addLayout(form)

        bb = QDialogButtonBox(_DBB_OK | _DBB_CANCEL)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _parse_multivalue(self, text: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for token in [t.strip() for t in (text or "").split(",") if t.strip()]:
            m = self._MV_RE.match(token)
            if not m:
                continue
            label = (m.group(1) or "").strip()
            if not label:
                continue
            cnt = int(m.group(2)) if m.group(2) else 1
            out[label] = max(1, cnt)
        return out

    def _collect_values(self, validate: bool) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for name, parent_chk, editor in self.rows:
            if not parent_chk.isChecked():
                continue

            if isinstance(editor, list):
                chosen: List[str] = []
                for c, s in editor:
                    if c.isEnabled() and c.isChecked():
                        n = max(1, int(s.value()))
                        chosen.append(f"{c.text().strip()}={n}")

                if not chosen:
                    if validate:
                        QMessageBox.warning(
                            self,
                            "Error",
                            f"Please select at least one subcategory for '{name}'."
                        )
                        raise ValueError(f"Please enter a value for '{name}'.")
                    continue

                out[name] = ", ".join(chosen)

            else:
                text = editor.text().strip()
                if not text:
                    if validate:
                        QMessageBox.warning(
                            self,
                            "Error",
                            f"Please enter a value for '{name}'"
                        )
                        raise ValueError(f"No input: {name}")
                    continue
                out[name] = text

        return out

    def values(self) -> Dict[str, str]:
        return self._collect_values(validate=True)

    def _current_values_no_validate(self) -> Dict[str, str]:
        return self._collect_values(validate=False)

    def accept(self):
        try:
            _ = self.values()
        except ValueError:
            return
        super().accept()

    def _load_presets(self) -> Dict[str, Dict[str, str]]:
        raw = self._settings.value(self._preset_key, "", type=str) or ""
        if not raw:
            return {}
        try:
            obj = json.loads(raw)
        except Exception:
            return {}
        if not isinstance(obj, dict):
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for k, v in obj.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k] = {str(kk): str(vv) for kk, vv in v.items()}
        return out

    def _save_presets(self) -> None:
        self._settings.setValue(
            self._preset_key,
            json.dumps(self._presets, ensure_ascii=False)
        )

    def _refresh_preset_combo(self) -> None:
        cur = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            for name in sorted(self._presets.keys()):
                self.preset_combo.addItem(name)
        finally:
            self.preset_combo.blockSignals(False)

        if cur:
            idx = self.preset_combo.findText(cur)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)

    def _apply_values_to_ui(self, vals: Dict[str, str]) -> None:
        for name, parent_chk, editor in self.rows:
            parent_chk.setChecked(False)
            if isinstance(editor, list):
                for c, s in editor:
                    c.setChecked(False)
                    s.setValue(1)
            else:
                editor.setText("")

        for name, parent_chk, editor in self.rows:
            if name not in vals:
                continue
            v = (vals.get(name, "") or "").strip()
            if not v:
                continue

            parent_chk.setChecked(True)

            if isinstance(editor, list):
                wants = self._parse_multivalue(v)
                for c, s in editor:
                    if c.text() in wants:
                        c.setChecked(True)
                        s.setValue(wants[c.text()])
            else:
                editor.setText(v)

    def _on_apply_preset(self) -> None:
        name = self.preset_combo.currentText().strip()
        if not name or name not in self._presets:
            return
        self._apply_values_to_ui(self._presets[name])

    def _on_save_preset(self) -> None:
        vals = self._current_values_no_validate()
        if not vals:
            QMessageBox.information(self, "Preset", "No selections to save.")
            return

        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return

        self._presets[name] = vals
        self._save_presets()
        self._refresh_preset_combo()

        idx = self.preset_combo.findText(name)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)

    def _on_delete_preset(self) -> None:
        name = self.preset_combo.currentText().strip()
        if not name or name not in self._presets:
            return

        reply = QMessageBox.question(
            self,
            "Delete preset",
            f"Delete preset '{name}'?",
            _MSG_YES | _MSG_NO,
            _MSG_NO
        )
        if reply != _MSG_YES:
            return

        del self._presets[name]
        self._save_presets()
        self._refresh_preset_combo()