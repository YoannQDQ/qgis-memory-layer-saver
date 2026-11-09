from qgis.PyQt.QtCore import QBuffer, QByteArray, QDataStream, QDate, QDateTime, QIODevice, QMetaType, QSaveFile, QTime

from .toolbox import log, log_warning, unfiltered_provider

try:
    from qgis.core import QgsUnsetAttributeValue
except ImportError:  # QGIS < 3.28
    QgsUnsetAttributeValue = None

INT16_MIN = -32768
INT16_MAX = 32767
# Values of these types are always streamed with their content
SAFE_TYPES = frozenset((int, float, str, bool, bytes, type(None), QDate, QTime, QDateTime, QByteArray))
STREAM_CHECK_MARKER = 0x4D4C5344


class UnstreamableValueError(OSError):
    """A value Qt would write without its content, so that nothing could read the file back"""


class Writer:
    """Write the committed data of memory layers. The target is only replaced by commit()."""

    def __init__(self, filename):
        self._filename = filename
        self._file = None
        self._dstream = None
        self._streamable = {}

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.close()

    def open(self):
        self._file = QSaveFile(self._filename)
        if not self._file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(f"Cannot open {self._filename}: {self._file.errorString()}")
        self._dstream = QDataStream(self._file)
        self._dstream.setVersion(QDataStream.Version.Qt_4_5)
        for c in b"QGis.MemoryLayerData":
            self._dstream.writeUInt8(c)
        # Version of MLD format
        self._dstream.writeUInt32(2)

    def commit(self):
        """Replace the target file with what has been written"""
        self.check_status()
        committed = self._file.commit()
        error = self._file.errorString()
        self._dstream = None
        self._file = None
        if not committed:
            raise OSError(f"Cannot write {self._filename}: {error}")

    def close(self):
        """Discard what has been written, the target file stays untouched"""
        if self._file is not None:
            self._file.cancelWriting()
        self._dstream = None
        self._file = None

    def check_status(self):
        if self._dstream.status() != QDataStream.Status.Ok:
            raise OSError(f"Cannot write {self._filename}: {self._file.errorString()}")

    def write_layers(self, layers):
        for layer in layers:
            self.write_layer(layer)
            self.check_status()

    def write_layer(self, layer):
        log("Writing layer " + layer.id())
        if not self._dstream:
            raise ValueError("Layer stream not open for reading")
        ds = self._dstream
        dp = layer.dataProvider()
        ss = layer.subsetString()
        attr = dp.attributeIndexes()
        ds.writeQString(layer.id())
        ds.writeQString(ss)
        ds.writeInt16(len(attr))
        flds = dp.fields()
        for fld in flds:
            ds.writeQString(fld.name())
            ds.writeInt16(self.type_id(layer, fld))
            ds.writeQString(fld.typeName())
            ds.writeInt16(self.clamped(layer, fld, "length", fld.length()))
            ds.writeInt16(self.clamped(layer, fld, "precision", fld.precision()))
            ds.writeQString(fld.comment())

        datetime_fields = [i for i, fld in enumerate(flds) if fld.type() == QMetaType.Type.QDateTime]

        # The provider only holds committed data, and the filter must not hide features from the file
        with unfiltered_provider(layer):
            for feat in dp.getFeatures():
                ds.writeBool(True)
                values = feat.attributes()
                for i in datetime_fields:
                    values[i] = self.local_datetime(values[i])
                for i, value in enumerate(values):
                    if type(value) not in SAFE_TYPES:
                        value = self.streamable(value, layer, flds[i])
                    ds.writeQVariant(value)
                geom = feat.geometry()
                if not geom:
                    ds.writeUInt32(0)
                else:
                    wkb = geom.asWkb()
                    ds.writeUInt32(len(wkb))
                    ds.writeRawData(wkb)
        ds.writeBool(False)

    def streamable(self, value, layer, field):
        """Qt writes the values it has no stream operator for without reporting any error, as their
        type only, and nothing can read the file back from there: such values must never be written.
        Unset values become NULL, as they are for the provider. Anything else stops the save."""
        if QgsUnsetAttributeValue is not None and isinstance(value, QgsUnsetAttributeValue):
            return None
        if isinstance(value, list):
            return [v if type(v) in SAFE_TYPES else self.streamable(v, layer, field) for v in value]
        if isinstance(value, dict):
            return {k: v if type(v) in SAFE_TYPES else self.streamable(v, layer, field) for k, v in value.items()}
        key = (type(value), value.userType() if hasattr(value, "userType") else None)
        if key not in self._streamable:
            self._streamable[key] = self.can_stream(value)
        if not self._streamable[key]:
            type_name = value.typeName() if hasattr(value, "typeName") else type(value).__name__
            raise UnstreamableValueError(
                f"Layer {layer.name()}, field {field.name()}: values of type {type_name} cannot be saved. "
                "The previous data is kept. Change these values, or make the layer permanent."
            )
        return value

    def can_stream(self, value):
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        out = QDataStream(buffer)
        out.setVersion(self._dstream.version())
        out.writeQVariant(value)
        out.writeUInt32(STREAM_CHECK_MARKER)
        buffer.close()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        back = QDataStream(buffer)
        back.setVersion(out.version())
        back.readQVariant()
        marker = back.readUInt32()
        return back.status() == QDataStream.Status.Ok and marker == STREAM_CHECK_MARKER

    @staticmethod
    def local_datetime(value):
        """The Qt_4_5 stream drops the offset and keeps the clock time, which would move the value
        to another instant when read back. Local time keeps the instant."""
        if hasattr(value, "toLocalTime") and value.isValid():
            return value.toLocalTime()
        return value

    @staticmethod
    def clamped(layer, field, what, value):
        """Lengths and precisions which do not fit the format lose their declared value, not the data"""
        value = int(value)
        clamped = max(INT16_MIN, min(INT16_MAX, value))
        if clamped != value:
            log_warning(f"{layer.name()}.{field.name()}: {what} {value} stored as {clamped}")
        return clamped

    @staticmethod
    def type_id(layer, field):
        """User type ids start at 65536 with Qt6. The reader finds out about these from the type name."""
        value = int(field.type())
        if INT16_MIN <= value <= INT16_MAX:
            return value
        log_warning(f"{layer.name()}.{field.name()}: type {value} cannot be stored")
        return int(QMetaType.Type.UnknownType)
