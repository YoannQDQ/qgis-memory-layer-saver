from qgis.core import QgsFeature, QgsField, QgsGeometry
from qgis.PyQt.QtCore import QDataStream, QFile, QIODevice

from .toolbox import log


class Reader:
    def __init__(self, filename):
        self._filename = filename
        self._file = None
        self._dstream = None
        self._version = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def open(self):
        self._file = QFile(self._filename)
        if not self._file.open(QIODevice.ReadOnly):
            raise ValueError("Cannot open " + self._filename)
        self._dstream = QDataStream(self._file)
        self._dstream.setVersion(QDataStream.Qt_4_5)
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

    def read_layers(self, layers):
        if not self._dstream:
            raise ValueError("Layer stream not open for reading")
        ds = self._dstream

        while True:
            if ds.atEnd():
                return
            layer_id = ds.readQString()
            for layer in layers:
                if layer.id() == layer_id:
                    break
            else:
                log(f"Unknown layer {layer_id} in project. Skipping.")
                layer = None
            if layer is None:
                self.skip_layer()
            else:
                self.read_layer(layer)

    def read_layer(self, layer):
        log("Reading layer " + layer.id())
        ds = self._dstream
        dp = layer.dataProvider()
        if dp.featureCount() > 0:
            raise ValueError("Memory layer " + id + " is already loaded")
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
            fld = QgsField(name, qtype, typename, length, precision, comment)
            dp.addAttributes([fld])

        nullgeom = QgsGeometry()
        fields = dp.fields()
        while ds.readBool():
            feat = QgsFeature(fields)
            for i in attr:
                value = ds.readQVariant()
                if value is not None:
                    feat[i] = value

            wkb_size = ds.readUInt32()
            if wkb_size == 0:
                feat.setGeometry(nullgeom)
            else:
                geom = QgsGeometry()
                geom.fromWkb(ds.readRawData(wkb_size))
                feat.setGeometry(geom)
            dp.addFeatures([feat])
        layer.setSubsetString(ss)
        layer.updateFields()
        layer.updateExtents()

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
        while ds.readBool():
            for _i in attr:
                ds.readQVariant()
            wkb_size = ds.readUInt32()
            if wkb_size > 0:
                ds.readRawData(wkb_size)
