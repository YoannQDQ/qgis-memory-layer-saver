from qgis.core import Qgis, QgsMessageLog


def log(msg, level=Qgis.Info):
    QgsMessageLog.logMessage(msg, "MemoryLayerSaver", level)


def log_info(msg):
    log(msg)


def log_warning(msg):
    log(msg, Qgis.Warning)


def log_error(msg):
    log(msg, Qgis.Critical)
