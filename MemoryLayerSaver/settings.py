from qgis.core import Qgis, QgsMapLayer, QgsSettings

# Used by QGIS to prompt user to save memory layers on exit.
ASK_TO_SAVE_MEMORY_LAYER_KEY = "askToSaveMemoryLayers"
# Used by MLS to save the original value of ASK_TO_SAVE_MEMORY_LAYER_KEY.
BACKUP_KEY = "MemoryLayerSaver/memoryLayerSaveSetting"
# Can be used to disable saving on a per-layer basis.
SAVE_LAYER_KEY = "SaveMemoryProvider"
# Wheter the mldata is embedded in the attachment.zip(qgs)/the project file (qgz)
# or stored in a separate .mldata file (legacy). This can be changed from
# the settings dialog, to export a project that can be opened in older QGIS versions (< 3.22).
MLDATA_EMBEDDED = "MemoryLayerSaver/mldataEmbedded"


class Settings:
    @classmethod
    def get_settings(cls):
        settings = QgsSettings()
        settings.beginGroup("MemoryLayerSaver", QgsSettings.Plugins)
        return settings

    @classmethod
    def ask_to_save_memory_layers(cls):
        return QgsSettings().value(ASK_TO_SAVE_MEMORY_LAYER_KEY, True, section=QgsSettings.App)

    @classmethod
    def set_ask_to_save_memory_layers(cls, value):
        cls.set_backup_ask_to_save_memory_layers(cls.backup_ask_to_save_memory_layers())
        QgsSettings().setValue(ASK_TO_SAVE_MEMORY_LAYER_KEY, value, section=QgsSettings.App)

    @classmethod
    def backup_ask_to_save_memory_layers(cls):
        return cls.get_settings().value(BACKUP_KEY, cls.ask_to_save_memory_layers(), bool)

    @classmethod
    def set_backup_ask_to_save_memory_layers(cls, value):
        cls.get_settings().setValue(BACKUP_KEY, value)

    @classmethod
    def mldata_embedded(cls):
        return cls.get_settings().value(MLDATA_EMBEDDED, True, bool)

    @classmethod
    def set_mldata_embedded(cls, value):
        cls.get_settings().setValue(MLDATA_EMBEDDED, value)

    @classmethod
    def legacy_mode(cls):
        """Whether to use the legacy .mldata file format"""
        return Qgis.QGIS_VERSION_INT < 32200 or not cls.mldata_embedded()

    @staticmethod
    def is_saved_layer(layer):
        if layer.type() != QgsMapLayer.VectorLayer:
            return
        data_provider = layer.dataProvider()
        if data_provider is None or data_provider.name() != "memory":
            return False
        return layer.customProperty(SAVE_LAYER_KEY, True) in [True, "true", "True"]
