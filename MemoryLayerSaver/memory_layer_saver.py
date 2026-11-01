import configparser
from pathlib import Path

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtCore import QFile, QFileInfo
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox, QStyle, QWidget
from qgis.utils import iface

from MemoryLayerSaver.layer_connector import LayerConnector
from MemoryLayerSaver.reader import Reader
from MemoryLayerSaver.settings import Settings
from MemoryLayerSaver.settings_dialog import SettingsDialog
from MemoryLayerSaver.toolbox import log, log_error
from MemoryLayerSaver.writer import Writer

DIR_PLUGIN_ROOT: Path = Path(__file__).parent


class MemoryLayerSaver(LayerConnector):
    def __init__(self):
        super().__init__()
        proj = QgsProject.instance()
        self.has_modified_layers = proj.isDirty()
        self.load_failed = False
        self._loading = False
        self._fingerprints = {}
        self._stored_legacy_mode = None
        self._has_orphaned_data = False

        proj.readProject.connect(self.load_data)
        proj.writeProject.connect(self.save_data)
        proj.cleared.connect(self.on_cleared)

    def initGui(self):  # noqa
        self.menu = iface.pluginMenu().addMenu(QIcon(str(DIR_PLUGIN_ROOT / "icon.svg")), self.tr("Memory layer saver"))
        self.menu.setObjectName("memory_layer_saver_menu")

        self.about_action = self.menu.addAction(
            self.menu.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation), self.tr("About")
        )
        self.about_action.setObjectName("memory_layer_saver_about")
        self.about_action.triggered.connect(self.show_about)

        self.info_action = self.menu.addAction(
            QIcon(str(DIR_PLUGIN_ROOT / "icon.svg")), self.tr("Display memory layer information")
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
        self.menu.deleteLater()
        self.detach()
        proj = QgsProject.instance()
        proj.readProject.disconnect(self.load_data)
        proj.writeProject.disconnect(self.save_data)
        proj.cleared.disconnect(self.on_cleared)

        # Restore the original value of the setting
        Settings.set_ask_to_save_memory_layers(Settings.backup_ask_to_save_memory_layers())
        log("MemoryLayerSaver unloaded")

    def on_cleared(self):
        """Called when the project is cleared (new project)"""
        self.has_modified_layers = False
        self.load_failed = False
        self._fingerprints = {}
        self._stored_legacy_mode = None
        self._has_orphaned_data = False

    def layer_signals(self, layer):
        return (
            layer.committedAttributesDeleted,
            layer.committedAttributesAdded,
            layer.committedFeaturesRemoved,
            layer.committedFeaturesAdded,
            layer.committedAttributeValuesChanges,
            layer.committedGeometriesChanges,
            # There is no specific signal for the renaming of fields
            layer.afterCommitChanges,
            layer.subsetStringChanged,
        )

    def connect_layer(self, layer):
        if Settings.is_saved_layer(layer):
            for signal in self.layer_signals(layer):
                signal.connect(self.set_project_dirty)
            layer.dataSourceChanged.connect(self.on_data_source_changed)
            # Connect layer will be called when a layer is added to the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True

    def disconnect_layer(self, layer):
        try:
            for signal in self.layer_signals(layer):
                signal.disconnect(self.set_project_dirty)
            layer.dataSourceChanged.disconnect(self.on_data_source_changed)
            # Disconnect layer will be called when a layer is removed from the project
            # So we set the has_modified_layers flag to ensure the mldata file will be
            # updated when the project is saved
            self.has_modified_layers = True
        except (AttributeError, TypeError):  # layer was not previously connected
            pass

    def load_data(self):
        """Load the memory layers from the .mldata file"""
        self.load_failed = False
        orphaned = False
        filepath = self.memory_layer_file()
        layers = list(self.memory_layers())
        # Projects without memory layers used to get an empty file: this is not an error
        if QFileInfo(filepath).size() > 0 and layers:
            log(f"Loading memory layers from {filepath} ({len(layers)} layers)")
            self._loading = True
            try:
                with Reader(filepath) as reader:
                    reader.read_layers(layers)
                    orphaned = reader.has_unknown_layers
            except Exception as e:
                # From there on the file holds more than the layers, it must not be overwritten
                self.load_failed = True
                log_error(f"Error reloading memory layers: {e}")
                QMessageBox.warning(
                    iface.mainWindow(),
                    self.tr("Error reloading memory layers"),
                    self.tr(
                        "Some memory layers could not be loaded and may be incomplete. The file was not changed, "
                        "and will not be overwritten unless you confirm it when saving the project.\n\n{0}"
                    ).format(e),
                )
            finally:
                self._loading = False

        self.set_stored_state(layers)
        self._has_orphaned_data = orphaned

    def save_data(self):
        """Write the layers to the .mldata file"""

        filepath = self.memory_layer_file(fallback_to_legacy=False)
        has_data = bool(filepath) and QFileInfo(filepath).size() > 0
        layers = list(self.memory_layers())

        # Without memory layers left, nothing has to stay in the project
        if not layers:
            if has_data and not self.may_overwrite():
                return
            self.remove_file(filepath)
            self.load_failed = False
            self.set_stored_state(layers)
            return

        # Check if the mldata file exists and if any memory layer has been modified
        if has_data and not self.needs_rewrite(layers):
            return
        if has_data and not self.may_overwrite():
            return

        # If mldata file do not exist in the attached files, create it
        created = False
        if not Settings.legacy_mode() and not filepath:
            QgsProject.instance().createAttachedFile("layers.mldata")
            created = True

        filepath = self.memory_layer_file()
        log(f"Saving memory layers to {filepath} ({len(layers)} layers)")
        try:
            with Writer(filepath) as writer:
                writer.write_layers(layers)
        except Exception as e:
            # The previous file is still there, unchanged
            self.has_modified_layers = True
            log_error(f"Error saving memory layers: {e}")
            if created:
                self.remove_file(filepath)
            iface.messageBar().pushMessage(
                self.tr("Memory Layer Saver"),
                self.tr("The memory layers could not be saved: {0}").format(e),
                Qgis.MessageLevel.Critical,
                0,
            )
            return

        self.load_failed = False
        self.set_stored_state(layers)

    @staticmethod
    def fingerprint(layer):
        """Catches what no signal reports, like most of the edits made directly on the data provider"""
        dp = layer.dataProvider()
        return dp.featureCount(), dp.extent().toString(), tuple(field.name() for field in dp.fields())

    def set_stored_state(self, layers):
        """Remember what the file holds, to detect the changes"""
        self.has_modified_layers = False
        self._stored_legacy_mode = Settings.legacy_mode()
        self._fingerprints = {layer.id(): self.fingerprint(layer) for layer in layers}
        self._has_orphaned_data = False

    def needs_rewrite(self, layers):
        return (
            self.has_modified_layers
            or self._has_orphaned_data
            or self._stored_legacy_mode != Settings.legacy_mode()
            or self._fingerprints != {layer.id(): self.fingerprint(layer) for layer in layers}
        )

    def may_overwrite(self):
        """After a failed load, the file holds more than the layers: only the user can decide to replace it"""
        if not self.load_failed:
            return True
        answer = QMessageBox.warning(
            iface.mainWindow(),
            self.tr("Memory Layer Saver"),
            self.tr(
                "The data of this project could not be loaded completely when it was opened. "
                "Saving the memory layers now would replace it with what was loaded.\n\n"
                "Replace it? If you choose No, the file stays as it is and "
                "the changes made to memory layers are not saved."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            return True
        # Stay flagged: the changes are still not stored anywhere
        self.has_modified_layers = True
        iface.messageBar().pushMessage(
            self.tr("Memory Layer Saver"),
            self.tr("The memory layers were not saved, the existing file was kept."),
            Qgis.MessageLevel.Warning,
            0,
        )
        return False

    @staticmethod
    def remove_file(filepath):
        if not filepath:
            return
        if Settings.legacy_mode():
            QFile.remove(filepath)
        else:
            QgsProject.instance().removeAttachedFile(filepath)

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
        if self._loading:
            return
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
        bogus.setWindowIcon(QIcon(str(DIR_PLUGIN_ROOT / "icon.svg")))

        # Get plugin metadata
        cfg = configparser.ConfigParser()
        cfg.read(DIR_PLUGIN_ROOT / "metadata.txt")

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
            "<b>{}</b> : <a href={}>GitHub</a><br>"
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
        SettingsDialog(iface.mainWindow()).exec()
