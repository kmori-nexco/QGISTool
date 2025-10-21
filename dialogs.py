from typing import Dict, List, Optional, Tuple
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox,
    QCheckBox, QComboBox, QLineEdit
)

class AttrDialog(QDialog):
    def __init__(self, parent, attrs_spec: List[Tuple[str, Optional[List[str]]]], last_values: Dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("属性を選択")
        self.rows = []
        lay = QVBoxLayout(self)
        form = QFormLayout()
        for name, options in attrs_spec:
            chk = QCheckBox(name)
            if options:
                editor = QComboBox(); editor.addItems(options)
            else:
                editor = QLineEdit()
            val = last_values.get(name, "")
            if isinstance(editor, QComboBox) and val:
                i = editor.findText(val)
                if i >= 0: editor.setCurrentIndex(i)
            elif isinstance(editor, QLineEdit):
                editor.setText(val)
            editor.setEnabled(False)
            chk.toggled.connect(editor.setEnabled)
            form.addRow(chk, editor)
            self.rows.append((name, chk, editor))
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> Dict[str, str]:
        out = {}
        for name, chk, editor in self.rows:
            if chk.isChecked():
                if isinstance(editor, QComboBox):
                    out[name] = editor.currentText().strip()
                else:
                    out[name] = editor.text().strip()
        return out
