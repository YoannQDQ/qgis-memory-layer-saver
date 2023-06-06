from qgis.PyQt.QtCore import QDataStream, QFile, QIODevice


class Writer:
    def __init__(self, filename):
        self._filename = filename
        self._file = None
        self._dstream = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close

    def open(self):
        self._file = QFile(self._filename)
        if not self._file.open(QIODevice.WriteOnly):
            raise ValueError("Cannot open " + self._filename)
        self._dstream = QDataStream(self._file)
        self._dstream.setVersion(QDataStream.Qt_4_5)
        for c in b"QGis.MemoryLayerData":
            self._dstream.writeUInt8(c)
        # Version of MLD format
        self._dstream.writeUInt32(2)

    def close(self):
        try:
            self._dstream.setDevice(None)
            self._file.close()
        except:
            pass
        self._dstream = None
        self._file = None

    def writeLayers(self, layers):
        for layer in layers:
            self.writeLayer(layer)

    def writeLayer(self, layer):
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
        fldnames = []
        for fld in flds:
            fldnames.append(fld.name())
            ds.writeQString(fld.name())
            ds.writeInt16(int(fld.type()))
            ds.writeQString(fld.typeName())
            ds.writeInt16(fld.length())
            ds.writeInt16(fld.precision())
            ds.writeQString(fld.comment())

        layer.setSubsetString("")
        feats = layer.getFeatures()
        for feat in feats:
            ds.writeBool(True)
            if attr:
                for field in fldnames:
                    try:
                        ds.writeQVariant(feat[field])
                    except KeyError:
                        ds.writeQVariant(None)
            geom = feat.geometry()
            if not geom:
                ds.writeUInt32(0)
            else:
                wkb = geom.asWkb()
                ds.writeUInt32(len(wkb))
                ds.writeRawData(wkb)
        ds.writeBool(False)
        layer.setSubsetString(ss)
