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

        while True:
            if ds.atEnd():
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
                    value = ds.readQVariant()
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
                dp.addFeatures([feat])
        finally:
            # Whatever precedes an error is worth having
            layer.setSubsetString(ss)
            layer.updateFields()
            layer.updateExtents()

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
                ds.readQVariant()
            self.read_wkb()
            self.check_status()
