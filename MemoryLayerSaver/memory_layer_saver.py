import configparser
import sys
from pathlib import Path

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtCore import QFile
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox, QStyle, QWidget
from qgis.utils import iface

from . import resources_rc  # noqa
from .layer_connector import LayerConnector
from .reader import Reader
from .settings import Settings
from .settings_dialog import SettingsDialog
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
        self.menu = iface.pluginMenu().addMenu(
            QIcon(":/plugins/memory_layer_saver/icon.svg"), self.tr("Memory layer saver")
        )
        self.menu.setObjectName("memory_layer_saver_menu")

        self.about_action = self.menu.addAction(
            self.menu.style().standardIcon(QStyle.SP_MessageBoxInformation), self.tr("About")
        )
        self.about_action.setObjectName("memory_layer_saver_about")
        self.about_action.triggered.connect(self.show_about)

        self.info_action = self.menu.addAction(
            QIcon(":/plugins/memory_layer_saver/icon.svg"), self.tr("Display memory layer information")
        )
        self.info_action.setObjectName("memory_layer_saver_info")
        self.info_action.triggered.connect(self.show_info)

        self.settings_action = self.menu.addAction(
            QgsApplication.getThemeIcon("mActionOptions.svg"), self.tr("Settings")
        )
        self.settings_action.setObjectName("memory_layer_saver_settings")
        self.settings_action.triggered.connect(self.show_settings)

        # Disable the prompt to save memory layers on exit since we are saving them automatically
        Settings.set_ask_to_save_memory_layers(False)
        log("MemoryLayerSaver loaded")

    def tr(self, message, *args, **kwargs):
        """Get the translation for a string using Qt translation API."""
        return QgsApplication.translate("MemoryLayerSaver", message, *args, **kwargs)

    def unload(self):
        iface.pluginMenu().removeAction(self.menu.menuAction())
        self.detach()
        proj = QgsProject.instance()
        proj.readProject.disconnect(self.load_data)
        proj.writeProject.disconnect(self.save_data)

        # Restore the original value of the setting
        Settings.set_ask_to_save_memory_layers(Settings.backup_ask_to_save_memory_layers())
        log("MemoryLayerSaver unloaded")

    def on_cleared(self):
        """Called when the project is cleared (new project)"""
        self.has_modified_layers = False

    def connect_layer(self, layer):
        if Settings.is_saved_layer(layer):
            layer.committedAttributesDeleted.connect(self.set_project_dirty)
            layer.committedAttributesAdded.connect(self.set_project_dirty)
            layer.committedFeaturesRemoved.connect(self.set_project_dirty)
            layer.committedFeaturesAdded.connect(self.set_project_dirty)
            layer.committedAttributeValuesChanges.connect(self.set_project_dirty)
            layer.committedGeometriesChanges.connect(self.set_project_dirty)
            layer.dataSourceChanged.connect(self.on_data_source_changed)
            # Connect layer will be called when a layer is added to the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True

    def disconnect_layer(self, layer):
        try:
            layer.committedAttributesDeleted.disconnect(self.set_project_dirty)
            layer.committedAttributesAdded.disconnect(self.set_project_dirty)
            layer.committedFeaturesRemoved.disconnect(self.set_project_dirty)
            layer.committedFeaturesAdded.disconnect(self.set_project_dirty)
            layer.committedAttributeValuesChanges.disconnect(self.set_project_dirty)
            layer.committedGeometriesChanges.disconnect(self.set_project_dirty)
            layer.dataSourceChanged.disconnect(self.on_data_source_changed)
            # Disconnect layer will be called when a layer is removed from the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True
        except (AttributeError, TypeError):  # layer was not previously connected
            pass

    def load_data(self):
        """Load the memory layers from the .mldata file"""
        filepath = self.memory_layer_file()
        file = QFile(filepath)
        if file.exists():
            layers = list(self.memory_layers())
            log(f"Loading memory layers from {filepath} ({len(layers)} layers)")
            if layers:
                try:
                    with Reader(filepath) as reader:
                        reader.read_layers(layers)
                except BaseException:
                    QMessageBox.information(
                        iface.mainWindow(), self.tr("Error reloading memory layers"), str(sys.exc_info()[1])
                    )

        self.has_modified_layers = False

    def save_data(self):
        """Write the layers to the .mldata file"""

        # Check if the mldata file exists and if any memory layer has been modified
        filepath = self.memory_layer_file(fallback_to_legacy=False)
        if filepath and Path(filepath).exists() and not self.has_modified_layers:
            return

        # If mldata file do not exist in the attached files, create it
        if not Settings.legacy_mode() and not filepath:
            QgsProject.instance().createAttachedFile("layers.mldata")

        filepath = self.memory_layer_file()
        layers = list(self.memory_layers())
        log(f"Saving memory layers to {filepath} ({len(layers)} layers)")
        if layers:
            with Writer(filepath) as writer:
                writer.write_layers(layers)

        self.has_modified_layers = False

    def memory_layers(self):
        """Return a list of all memory layers in the project"""
        return [layer for layer in QgsProject.instance().mapLayers().values() if Settings.is_saved_layer(layer)]

    def legacy_memory_layer_file(self):
        """Returns the path to the legacy .mldata file"""
        return QgsProject.instance().fileName() + ".mldata"

    def memory_layer_file(self, fallback_to_legacy=True):
        """Returns the path to the .mldata file"""
        name = QgsProject.instance().fileName()
        if not name:
            return ""

        # Legacy mode (old QGIS versions or mldata stored in a separate file)
        if Settings.legacy_mode():
            return self.legacy_memory_layer_file()

        # Find the layers.mldata file in the attached files
        # Note attached files are prefixed with a random string hence the use of endswith
        for attachment in QgsProject.instance().attachedFiles():
            if attachment.endswith("layers.mldata"):
                return attachment

        # The layers.mldata was not found in the attached files
        if fallback_to_legacy:
            return self.legacy_memory_layer_file()

    def set_project_dirty(self):
        """Set project as dirty when a memory layer is modified"""
        self.has_modified_layers = True
        QgsProject.instance().setDirty(True)

    def on_data_source_changed(self):
        # If a temporary layer is made permanent, its data source will change
        # At this point, the layer is no longer a memory layer, so we disconnect from it
        layer = self.sender()
        if not Settings.is_saved_layer(layer):
            self.disconnect_layer(layer)

    def show_info(self):
        """Display some information about the memory layers"""
        layer_info = [(layer.name(), layer.featureCount()) for layer in self.memory_layers()]
        if layer_info:
            message = self.tr("The following memory layers will be saved with this project:")
            message += "<br>"
            message += "<br>".join(
                self.tr("- <b>{0}</b> ({1} features)", "Layer name and number of features", n=count).format(name, count)
                for name, count in layer_info
            )
        else:
            message = self.tr("This project contains no memory layers to be saved")
        QMessageBox.information(iface.mainWindow(), "Memory Layer Saver", message)

    def show_about(self):
        """Display the about message box"""
        # Used to display plugin icon in the about message box
        bogus = QWidget(iface.mainWindow())
        bogus.setWindowIcon(QIcon(":/plugins/memory_layer_saver/icon.svg"))

        # Get plugin metadata
        cfg = configparser.ConfigParser()
        cfg.read(Path(__file__).parent / "metadata.txt")

        name = cfg.get("general", "name")
        version = cfg.get("general", "version")
        repository = cfg.get("general", "repository")
        tracker = cfg.get("general", "tracker")
        homepage = cfg.get("general", "homepage")
        QMessageBox.about(
            bogus,
            self.tr("About {0}").format(name),
            "<b>Version</b> {}<br><br>"
            "<b>{}</b> : <a href={}>GitHub</a><br>"
            "<b>{}</b> : <a href={}/issues>GitHub</a><br>"
            "<b>{}</b> : <a href={}>GitHub</a>".format(
                version,
                self.tr("Source code"),
                repository,
                self.tr("Report issues"),
                tracker,
                self.tr("Documentation"),
                homepage,
            ),
        )
        bogus.deleteLater()

    def show_settings(self):
        """Show the settings dialog"""
        SettingsDialog().exec()
