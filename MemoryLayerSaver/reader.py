from qgis.core import QgsFeature, QgsField, QgsGeometry
from qgis.PyQt.QtCore import QDataStream, QFile, QIODevice, QMetaType

from .toolbox import log, log_warning

# The type names of the memory provider which imply a subtype, which the file does not store
NATIVE_SUBTYPES = {
    "integerlist": QMetaType.Type.Int,
    "integer64list": QMetaType.Type.LongLong,
    "doublelist": QMetaType.Type.Double,
    "stringlist": QMetaType.Type.QString,
    "map": QMetaType.Type.QString,
}


# Qt writes the values it has no stream operator for without any error, as their type id and type
# name only: nothing can read them back. These were checked to have none in QGIS 3.40 to 4.2, so their
# values are read as NULL. QgsUnsetAttributeValue (a default value not resolved yet) ends up in the
# attributes of some features. Geometries are not in the list: they have an operator in Qt 6 builds.
EMPTY_USER_TYPES = (
    b"QgsUnsetAttributeValue",
    b"QgsInterval",
    b"QgsCoordinateReferenceSystem",
    b"QgsPointXY",
)
# The type ids which announce a user type, followed by its name: Qt 4 streams, then Qt 5 streams
STREAM_USER_TYPE_IDS = (127, 1024)
# Containers, whose items are read one by one when one of them cannot be read
STREAM_TYPE_MAP = 8
STREAM_TYPE_LIST = 9
STREAM_TYPE_HASH = 28


class Reader:
    def __init__(self, filename):
        self._filename = filename
        self._file = None
        self._dstream = None
        self._version = None
        # Whether the file holds layers which are not memory layers of the project any more
        self.has_unknown_layers = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def open(self):
        self._file = QFile(self._filename)
        if not self._file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise ValueError("Cannot open " + self._filename)
        self._dstream = QDataStream(self._file)
        self._dstream.setVersion(QDataStream.Version.Qt_4_5)
        for c in b"QGis.MemoryLayerData":
            ct = self._dstream.readUInt8()
            if ct != c:
                raise ValueError(self._filename + " is not a valid memory layer data file")
        version = self._dstream.readInt32()
        if version not in (1, 2):
            raise ValueError(self._filename + " is not compatible with this version of the MemoryLayerSaver plugin")
        self._version = version

    def close(self):
        try:
            self._dstream.setDevice(None)
            self._file.close()
        except BaseException:
            pass
        self._dstream = None
        self._file = None

    def check_status(self):
        """QDataStream returns zeros instead of raising when reading past the end or corrupt data"""
        if self._dstream.status() != QDataStream.Status.Ok:
            raise ValueError(self._filename + " is truncated or corrupt")

    def read_layers(self, layers):
        if not self._dstream:
            raise ValueError("Layer stream not open for reading")
        ds = self._dstream
        # Features the memory provider refused as they were, which were loaded with NULL values
        self.refused = []

        while True:
            if ds.atEnd():
                if self.refused:
                    # Values are missing: reported as a failed load, so that the file is not replaced without asking
                    shown = "; ".join(self.refused[:5])
                    more = f" (and {len(self.refused) - 5} more)" if len(self.refused) > 5 else ""
                    raise ValueError(f"{len(self.refused)} features needed changes to load: {shown}{more}")
                return
            layer_id = ds.readQString()
            self.check_status()
            for layer in layers:
                if layer.id() == layer_id:
                    break
            else:
                log(f"Unknown layer {layer_id} in project. Skipping.")
                self.has_unknown_layers = True
                layer = None
            if layer is None:
                self.skip_layer()
            else:
                self.read_layer(layer)
            self.check_status()

    def read_layer(self, layer):
        log("Reading layer " + layer.id())
        ds = self._dstream
        dp = layer.dataProvider()
        if dp.featureCount() > 0:
            log_warning("Memory layer " + layer.id() + " is already loaded. Skipping.")
            self.skip_layer()
            return
        attr = dp.attributeIndexes()
        dp.deleteAttributes(attr)
        ss = ""
        if self._version > 1:
            ss = ds.readQString()
        nattr = ds.readInt16()
        attr = list(range(nattr))
        for _i in attr:
            name = ds.readQString()
            qtype = ds.readInt16()
            typename = ds.readQString()
            length = ds.readInt16()
            precision = ds.readInt16()
            comment = ds.readQString()
            self.check_status()

            try:
                field_type = QMetaType.Type(qtype)
            except (TypeError, ValueError):
                # Fallback en cas d'échec
                field_type = QMetaType.Type.UnknownType
                log(f"Unable to convert type {qtype} for field {name}, using UnknownType")
            # User type ids depend on the Qt version
            if typename.lower() == "geometry":
                field_type = QMetaType.Type.User

            subtype = NATIVE_SUBTYPES.get(typename.lower(), QMetaType.Type.UnknownType)
            fld = QgsField(name, field_type, typename, int(length), int(precision), comment, subtype)
            dp.addAttributes([fld])

        nullgeom = QgsGeometry()
        fields = dp.fields()
        try:
            while ds.readBool():
                feat = QgsFeature(fields)
                for i in attr:
                    value = self.read_value()
                    if value is not None:
                        feat[i] = value

                wkb = self.read_wkb()
                self.check_status()
                if wkb is None:
                    feat.setGeometry(nullgeom)
                else:
                    geom = QgsGeometry()
                    geom.fromWkb(wkb)
                    feat.setGeometry(geom)
                self.add_feature(layer, feat)
        finally:
            # Whatever precedes an error is worth having
            layer.setSubsetString(ss)
            layer.updateFields()
            layer.updateExtents()

    def add_feature(self, layer, feat):
        """The memory provider refuses a feature with a value it cannot convert to the type of its
        field: its incompatible values are made NULL, as losing a value is better than losing the feature"""
        dp = layer.dataProvider()
        if dp.addFeatures([feat])[0]:
            return
        error = dp.lastError()
        values = []
        for field, value in zip(dp.fields(), feat.attributes()):
            try:
                values.append(field.convertCompatible(value))
            except (TypeError, ValueError):
                values.append(None)
        feat.setAttributes(values)
        if not dp.addFeatures([feat])[0]:
            feat.setAttributes([None] * len(values))
            error += " (all its values were dropped)"
            if not dp.addFeatures([feat])[0]:
                raise ValueError(f"A feature of {layer.name()} was refused by the memory provider: {dp.lastError()}")
        log_warning(f"{layer.name()}: {error}")
        self.refused.append(f"{layer.name()}: {error}")

    def read_value(self):
        ds = self._dstream
        position = self._file.pos()
        value = ds.readQVariant()
        if ds.status() != QDataStream.Status.Ok:
            # The slow way, for the values Qt cannot read
            self._file.seek(position)
            ds.resetStatus()
            value = self.read_value_slowly()
        return value

    def read_value_slowly(self):
        """Values of some user types were written without any content (see EMPTY_USER_TYPES): Qt
        reads their type and refuses them, they are read as NULL here, also inside lists and maps.
        Anything else Qt cannot read is an error."""
        ds = self._dstream
        position = self._file.pos()
        type_id = ds.readUInt32()
        ds.readInt8()  # is null
        self.check_status()
        if type_id in STREAM_USER_TYPE_IDS:
            name = bytes(ds.readBytes() or b"").rstrip(b"\0")
            self.check_status()
            if name in EMPTY_USER_TYPES:
                return None
            raise ValueError(f"{self._filename}: values of type {name.decode(errors='replace')} cannot be read")
        if type_id in (STREAM_TYPE_LIST, STREAM_TYPE_MAP, STREAM_TYPE_HASH):
            count = ds.readUInt32()
            self.check_status()
            if count > self._file.size():
                raise ValueError(self._filename + " is truncated or corrupt")
            if type_id == STREAM_TYPE_LIST:
                return [self.read_value_slowly() for _i in range(count)]
            result = {}
            for _i in range(count):
                key = ds.readQString()
                result[key] = self.read_value_slowly()
            return result
        self._file.seek(position)
        value = ds.readQVariant()
        self.check_status()
        return value

    def read_wkb(self):
        ds = self._dstream
        wkb_size = ds.readUInt32()
        # Do not try to allocate what corrupt data asks for
        if wkb_size > self._file.size():
            raise ValueError(self._filename + " is truncated or corrupt")
        return ds.readRawData(wkb_size) if wkb_size > 0 else None

    def skip_layer(self):
        ds = self._dstream
        if self._version > 1:
            ds.readQString()  # subset string
        nattr = ds.readInt16()
        attr = list(range(nattr))
        for _i in attr:
            ds.readQString()  # name
            ds.readInt16()  # type
            ds.readQString()  # typename
            ds.readInt16()  # length
            ds.readInt16()  # precision
            ds.readQString()  # comment
            self.check_status()
        while ds.readBool():
            for _i in attr:
                self.read_value()
            self.read_wkb()
            self.check_status()
