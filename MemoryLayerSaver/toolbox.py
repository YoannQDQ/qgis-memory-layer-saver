from qgis.core import Qgis, QgsMessageLog


def log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(msg, "MemoryLayerSaver", level)


def log_info(msg):
    log(msg)


def log_warning(msg):
    log(msg, Qgis.MessageLevel.Warning)


def log_error(msg):
    log(msg, Qgis.MessageLevel.Critical)
