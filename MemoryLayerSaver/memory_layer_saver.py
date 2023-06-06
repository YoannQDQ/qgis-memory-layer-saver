import sys

from qgis.core import QgsMapLayer, QgsProject
from qgis.PyQt.QtCore import QFile
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.utils import iface

from . import resources_rc  # noqa
from .layer_connector import LayerConnector
from .reader import Reader
from .settings import Settings
from .writer import Writer


class MemoryLayerSaver(LayerConnector):
    def __init__(self):
        super().__init__()
        proj = QgsProject.instance()
        proj.readProject.connect(self.load_data)
        proj.writeProject.connect(self.save_data)

    def initGui(self):  # noqa
        self.infoAction = QAction(
            QIcon(":/plugins/memory_layer_saver/memory_layer_saver.svg"),
            "Display memory layer information",
            iface.mainWindow(),
        )
        self.infoAction.triggered.connect(self.show_info)
        iface.addPluginToMenu("&Memory layer saver", self.infoAction)

        # Disable the prompt to save memory layers on exit since we are saving them automatically
        Settings.set_ask_to_save_memory_layers(False)

    def unload(self):
        iface.removePluginMenu("&Memory layer saver", self.infoAction)
        self.detach()
        proj = QgsProject.instance()
        proj.readProject.disconnect(self.load_data)
        proj.writeProject.disconnect(self.save_data)

        # Restore the original value of the setting
        Settings.set_ask_to_save_memory_layers(Settings.backup_ask_to_save_memory_layers())

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
                        reader.readLayers(layers)
                except:
                    QMessageBox.information(iface.mainWindow(), "Error reloading memory layers", str(sys.exc_info()[1]))

    def save_data(self):
        filename = self.memory_layer_file()
        layers = list(self.memory_layers())
        if layers:
            with Writer(filename) as writer:
                writer.writeLayers(layers)

    def memory_layers(self):
        return [layer for layer in QgsProject.instance().mapLayers().values() if Settings.is_saved_layer(layer)]

    def memory_layer_file(self):
        name = QgsProject.instance().fileName()
        if not name:
            return ""
        lname = name + ".mldata"
        return lname

    def set_project_dirty(self):
        QgsProject.instance().setDirty(True)

    def show_info(self):
        names = [layer.name() for layer in self.memory_layers()]
        if names:
            message = "The following memory data provider layers will be saved with this project:\n   "
            message += "\n   ".join(names)
        else:
            message = "This project contains no memory data provider layers to be saved"
        QMessageBox.information(iface.mainWindow(), "Memory layer saver info", message)
