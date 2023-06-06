import sys

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtCore import QFile
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.utils import iface

from . import resources_rc  # noqa
from .layer_connector import LayerConnector
from .reader import Reader
from .settings import Settings
from .toolbox import log
from .writer import Writer


class MemoryLayerSaver(LayerConnector):
    def __init__(self):
        super().__init__()
        proj = QgsProject.instance()
        self.has_modified_layers = proj.isDirty()

        proj.readProject.connect(self.load_data)
        proj.writeProject.connect(self.save_data)
        proj.cleared.connect(self.on_cleared)

    def initGui(self):  # noqa
        self.info_action = QAction(
            QIcon(":/plugins/memory_layer_saver/memory_layer_saver.svg"),
            self.tr("Display memory layer information"),
            iface.mainWindow(),
        )
        self.info_action.setObjectName("memory_layer_saver_info")
        self.info_action.triggered.connect(self.show_info)
        iface.addPluginToMenu("Memory layer saver", self.info_action)

        # Disable the prompt to save memory layers on exit since we are saving them automatically
        Settings.set_ask_to_save_memory_layers(False)
        log("MemoryLayerSaver loaded")

    def tr(self, message, *args, **kwargs):
        """Get the translation for a string using Qt translation API."""
        return QgsApplication.translate("MemoryLayerSaver", message, *args, **kwargs)

    def unload(self):
        iface.removePluginMenu("Memory layer saver", self.info_action)
        self.detach()
        proj = QgsProject.instance()
        proj.readProject.disconnect(self.load_data)
        proj.writeProject.disconnect(self.save_data)

        # Restore the original value of the setting
        Settings.set_ask_to_save_memory_layers(Settings.backup_ask_to_save_memory_layers())
        log("MemoryLayerSaver unloaded")

    def on_cleared(self):
        self.has_modified_layers = False

    def connect_layer(self, layer):
        if Settings.is_saved_layer(layer):
            layer.committedAttributesDeleted.connect(self.set_project_dirty)
            layer.committedAttributesAdded.connect(self.set_project_dirty)
            layer.committedFeaturesRemoved.connect(self.set_project_dirty)
            layer.committedFeaturesAdded.connect(self.set_project_dirty)
            layer.committedAttributeValuesChanges.connect(self.set_project_dirty)
            layer.committedGeometriesChanges.connect(self.set_project_dirty)

    def disconnect_layer(self, layer):
        if Settings.is_saved_layer(layer):
            layer.committedAttributesDeleted.disconnect(self.set_project_dirty)
            layer.committedAttributesAdded.disconnect(self.set_project_dirty)
            layer.committedFeaturesRemoved.disconnect(self.set_project_dirty)
            layer.committedFeaturesAdded.disconnect(self.set_project_dirty)
            layer.committedAttributeValuesChanges.disconnect(self.set_project_dirty)
            layer.committedGeometriesChanges.disconnect(self.set_project_dirty)

    def load_data(self):
        filename = self.memory_layer_file()
        file = QFile(filename)
        if file.exists():
            layers = list(self.memory_layers())
            if layers:
                try:
                    with Reader(filename) as reader:
                        reader.read_layers(layers)
                except BaseException:
                    QMessageBox.information(
                        iface.mainWindow(), self.tr("Error reloading memory layers"), str(sys.exc_info()[1])
                    )

    def save_data(self):
        if not self.has_modified_layers:
            return
        filename = self.memory_layer_file()
        layers = list(self.memory_layers())
        if layers:
            with Writer(filename) as writer:
                writer.write_layers(layers)

        self.has_modified_layers = False

    def memory_layers(self):
        return [layer for layer in QgsProject.instance().mapLayers().values() if Settings.is_saved_layer(layer)]

    def memory_layer_file(self):
        name = QgsProject.instance().fileName()
        if not name:
            return ""
        lname = name + ".mldata"
        return lname

    def set_project_dirty(self):
        self.has_modified_layers = True
        QgsProject.instance().setDirty(True)

    def show_info(self):
        layer_info = [(layer.name(), layer.featureCount()) for layer in self.memory_layers()]
        if layer_info:
            message = self.tr("The following memory data provider layers will be saved with this project:")
            message += "<br>"
            message += "<br>".join(
                self.tr("- <b>{}</b> ({} features)", "Layer name and number of features", n=count).format(name, count)
                for name, count in layer_info
            )
        else:
            message = self.tr("This project contains no memory data provider layers to be saved")
        QMessageBox.information(iface.mainWindow(), "Memory Layer Saver", message)
