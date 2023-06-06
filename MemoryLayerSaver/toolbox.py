from qgis.core import Qgis, QgsMessageLog


def log(msg, level=Qgis.Info):
    QgsMessageLog.logMessage(msg, "MemoryLayerSaver", level)
