# dialogs.py
from typing import Dict, List, Optional, Tuple
import re
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox,
    QCheckBox, QLineEdit, QWidget, QSpinBox,
    QVBoxLayout as QVLayout, QHBoxLayout, QMessageBox)

class AttrDialog(QDialog):
    def __init__(self, parent, attrs_spec: List[Tuple[str, Optional[List[str]]]], last_values: Dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Select attributes")

        self.rows = []
        lay = QVBoxLayout(self)
        form = QFormLayout()

        for name, options in attrs_spec:
            parent_chk = QCheckBox(name)

            if options:
                sub_container = QWidget()
                sub_layout = QVLayout(sub_container)
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
                    wants: Dict[str, int] = {}
                    for token in [t.strip() for t in last_val.split(",") if t.strip()]:
                        m = re.match(r"^(.*?)(?:\s*=\s*(\d+))?$", token)
                        if not m:
                            continue
                        label = (m.group(1) or "").strip()
                        cnt = int(m.group(2)) if m and m.group(2) else 1
                        if label:
                            wants[label] = max(1, cnt)
                    any_checked = False
                    for c, s in sub_items:
                        if c.text() in wants:
                            c.setChecked(True)
                            s.setValue(wants[c.text()])
                            any_checked = True
                    if any_checked:
                        parent_chk.setChecked(True)

                # 親ON/OFFで子セットまるごと有効/無効
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

        # PyQt5/6 互換: StandardButton があればそちらを使う
        _StdBtn = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)
        _OK = getattr(_StdBtn, "Ok", QDialogButtonBox.Ok)
        _CANCEL = getattr(_StdBtn, "Cancel", QDialogButtonBox.Cancel)
        bb = QDialogButtonBox(_OK | _CANCEL)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> Dict[str, str]:
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
                    QMessageBox.warning(self, "Error", f"Please select at least one subcategory for '{name}'.")
                    raise ValueError(f"Please enter a value for '{name}'.")
                out[name] = ", ".join(chosen)
            else:
                text = editor.text().strip()
                if not text:
                    QMessageBox.warning(self, "Error", f"Please enter a value for '{name}'")
                    raise ValueError(f"No input: {name}")
                out[name] = text
        return out

    def accept(self):
        try:
            _ = self.values()
        except ValueError:
            return
        super().accept()
