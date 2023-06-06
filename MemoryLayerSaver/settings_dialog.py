from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QVBoxLayout

from .settings import Settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)

        self.checkbox = QCheckBox(self.tr("Embbed mldata file"), self)
        self.checkbox.setChecked(Settings.mldata_embedded())
        self.checkbox.setToolTip(
            self.tr(
                "If checked, the mldata file will be embedded inside the project file (qgz) "
                "or in the attachment.zip (qgs). "
                "Otherwise, it will be stored in a separate .mldata file beside the project file."
            )
        )

        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout.addWidget(self.checkbox)
        layout.addStretch()
        layout.addWidget(button_box)

    def accept(self):
        Settings.set_mldata_embedded(self.checkbox.isChecked())
        super().accept()
