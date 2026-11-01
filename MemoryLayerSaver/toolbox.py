from contextlib import contextmanager

from qgis.core import Qgis, QgsMessageLog


@contextmanager
def unfiltered_provider(layer):
    """Clear the filter of a layer's provider without the layer noticing. Unlike
    QgsVectorLayer.setSubsetString, this is not refused when the layer is being edited."""
    dp = layer.dataProvider()
    subset = dp.subsetString()
    if not subset:
        yield
        return
    blocked = dp.blockSignals(True)
    try:
        dp.setSubsetString("", False)
        yield
    finally:
        dp.setSubsetString(subset, False)
        dp.blockSignals(blocked)


def log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(msg, "MemoryLayerSaver", level)


def log_info(msg):
    log(msg)


def log_warning(msg):
    log(msg, Qgis.MessageLevel.Warning)


def log_error(msg):
    log(msg, Qgis.MessageLevel.Critical)
