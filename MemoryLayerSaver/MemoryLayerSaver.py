from __future__ import with_statement

from PyQt4.QtGui import *
from PyQt4.QtCore import *
from qgis.core import *
import sys


class Writer( QObject ):

    def __init__( self, filename ):
        QObject.__init__( self, None )
        self._filename = filename
        self._file = None
        self._dstream = None

    def __enter__( self ):
        self.open()
        return self

    def __exit__( self, exc_type, exc_value, traceback ):
        self.close

    def open( self ):
        self._file = QFile(self._filename)
        if not self._file.open(QIODevice.WriteOnly):
            raise ValueError("Cannot open "+self._filename)
        self._dstream = QDataStream( self._file )
        self._dstream.setVersion(QDataStream.Qt_4_5)
        for c in "QGis.MemoryLayerData":
            self._dstream.writeUInt8(c)
        # Version of MLD format
        self._dstream.writeUInt32(1)


    def close( self ):
        try:
            self._dstream.setDevice(None)
            self._file.close()
        except:
            pass
        self._dstream=None
        self._file=None
        
    def writeLayers( self, layers ):
        for layer in layers:
            self.writeLayer( layer )

    def writeLayer( self, layer ):
        if not self._dstream:
            raise ValueError("Layer stream not open for reading")
        ds=self._dstream
        dp = layer.dataProvider()
        attr=dp.attributeIndexes()
        dp.select(attr)
        ds.writeQString(layer.id())
        ds.writeInt16(len(attr))
        flds = dp.fields()
        attr=sorted(flds.keys())
        for i in attr:
            fld=dp.fields()[i]
            ds.writeQString(fld.name())
            ds.writeInt16(int(fld.type()))
            ds.writeQString(fld.typeName())
            ds.writeInt16(fld.length())
            ds.writeInt16(fld.precision())
            ds.writeQString(fld.comment())
        feat=QgsFeature()
        while dp.nextFeature(feat):
            ds.writeBool(True)
            if attr:
                fmap = feat.attributeMap()
                for i in attr:
                    if i in fmap:
                        ds.writeQVariant(fmap[i])
                    else:
                        ds.writeQVariant(QVariant())
            geom = feat.geometry()
            if not geom:
                ds.writeUInt32(0)
            else:
                ds.writeUInt32(geom.wkbSize())
                ds.writeRawData(geom.asWkb())
                print "Writing geom:",geom.exportToWkt()
        ds.writeBool(False)

class Reader( QObject ):

    def __init__( self, filename ):
        self._filename = filename
        self._file=None
        self._dstream=None

    def __enter__( self ):
        self.open()
        return self

    def __exit__( self, exc_type, exc_value, traceback ):
        self.close

    def open( self ):
        self._file = QFile(self._filename)
        if not self._file.open(QIODevice.ReadOnly):
            raise ValueError("Cannot open "+self._filename)
        self._dstream = QDataStream( self._file )
        self._dstream.setVersion(QDataStream.Qt_4_5)
        for c in "QGis.MemoryLayerData":

            ct = self._dstream.readUInt8()
            if ct != c:
                raise ValueError(self._filename + " is not a valid memory layer data file")
        version = self._dstream.readInt32()
        if version != 1:
            raise ValueError(self._filename + " is not compatible with this version of the MemoryLayerSaver plugin")


    def close( self ):
        try:
            self._dstream.setDevice(None)
            self._file.close()
        except:
            pass
        self._dstream=None
        self._file=None

    def readLayers( self, layers ):
        if not self._dstream:
            raise ValueError("Layer stream not open for reading")
        ds=self._dstream

        while True:
            if ds.atEnd():
                return 
            id=ds.readQString()
            layer = None
            for l in layers:
                if l.id() == id:
                    layer = l
            if not layer:
                 self.skipLayer()
            else:
                self.readLayer( layer )

    def readLayer( self, layer ):
        ds=self._dstream
        dp = layer.dataProvider()
        if dp.featureCount() > 0:
            raise ValueError(u"Memory layer "+id+" is already loaded")
        attr=dp.attributeIndexes()
        dp.deleteAttributes(attr)

        nattr = ds.readInt16()
        attr=range(nattr)
        for i in attr:
            name=ds.readQString()
            qtype=ds.readInt16()
            typename=ds.readQString()
            length=ds.readInt16()
            precision=ds.readInt16()
            comment=ds.readQString()
            fld=QgsField(name,qtype,typename,length,precision,comment)
            dp.addAttributes([fld])

        nulgeom=QgsGeometry()
        while ds.readBool():
            feat=QgsFeature()
            fmap={}
            for i in attr:
                fmap[i]=ds.readQVariant()
            feat.setAttributeMap(fmap)

            wkbSize = ds.readUInt32()
            if wkbSize == 0:
                feat.setGeometry(nullgeom)
            else:
                geom=QgsGeometry()
                geom.fromWkb(ds.readRawData(wkbSize))
                feat.setGeometry(geom)
                print "Geometry set!", geom.exportToWkt()
            dp.addFeatures([feat])
        if 'updateFieldMap' in dir(layer):
            layer.updateFieldMap()
        layer.updateExtents()

    def skipLayer( self ):
        ds=self._dstream
        nattr = ds.readInt16()
        attr=range(nattr)
        for i in attr:
            name=ds.readQString()
            qtype=ds.readInt16()
            typename=ds.readQString()
            length=ds.readInt16()
            precision=ds.readInt16()
            comment=ds.readQString()
        while ds.readBool():
            for i in attr:
                ds.readQVariant()
            wkbSize = ds.readUInt32()
            if wkbSize > 0:
                ds.readRawData(wkbSize)

class MemoryLayerSaver:

    def __init__( self, iface ):
        self._iface = iface
        version = QGis.QGIS_VERSION_INT
        self._deleteSignalOk = version >= 10700

    def attachToProject(self):
        self.connectToProject()
        self.connectMemoryLayers()

    def detachFromProject(self):      
        # Following line OK in 1.7
        # Cannot delete memory files in QGis 1.6 as they get deleted
        # on project exit.
        # self.deleteMemoryDataFiles()
        self.disconnectFromProject()
        self.disconnectMemoryLayers()
        pass

    def connectToProject(self):
        proj = QgsProject.instance()
        QObject.connect(proj, SIGNAL("readProject(const QDomDocument &)"),self.loadData)
        QObject.connect(proj, SIGNAL("writeProject(QDomDocument &)"),self.saveData)
        QObject.connect(QgsMapLayerRegistry.instance(), SIGNAL("layerWasAdded(QgsMapLayer *)"),self.connectProvider)

    def disconnectFromProject(self):
        proj = QgsProject.instance()
        QObject.disconnect(proj, SIGNAL("readProject(const QDomDocument &)"),self.loadData)
        QObject.disconnect(proj, SIGNAL("writeProject(QDomDocument &)"),self.saveData)
        QObject.disconnect(QgsMapLayerRegistry.instance(), SIGNAL("layerWasAdded(QgsMapLayer *)"),self.connectProvider)

    def connectProvider( self, layer ):
        if self.isSavedLayer(layer):
            QObject.connect(layer, SIGNAL("committedAttributesDeleted(const QString &, const QgsAttributeIds &)"),self.setProjectDirty2)
            QObject.connect(layer, SIGNAL("committedAttributesAdded(const QString &, const QList<QgsField> &)"),self.setProjectDirty2)
            if self._deleteSignalOk:
                QObject.connect(layer, SIGNAL("committedFeaturesRemoved(const QString &, const QgsFeatureIds & )"),self.setProjectDirty2)
            QObject.connect(layer, SIGNAL("committedFeaturesAdded(const QString &, const QgsFeatureList &)"),self.setProjectDirty2)
            QObject.connect(layer, SIGNAL("committedAttributeValuesChanges(const QString &, const QgsChangedAttributesMap &)"),self.setProjectDirty2)
            QObject.connect(layer, SIGNAL("committedGeometriesChanges(const QString &, const QgsGeometryMap &)"),self.setProjectDirty2)

    def disconnectProvider( self, layer ):
        if self.isSavedLayer(layer):
            QObject.disconnect(layer, SIGNAL("committedAttributesDeleted(const QString &, const QgsAttributeIds &)"),self.setProjectDirty2)
            QObject.disconnect(layer, SIGNAL("committedAttributesAdded(const QString &, const QList<QgsField> &)"),self.setProjectDirty2)
            if self._deleteSignalOk:
                QObject.disconnect(layer, SIGNAL("committedFeaturesRemoved(const QString &, const QgsFeatureIds & )"),self.setProjectDirty2)
            QObject.disconnect(layer, SIGNAL("committedFeaturesAdded(const QString &, const QgsFeatureList &)"),self.setProjectDirty2)
            QObject.disconnect(layer, SIGNAL("committedAttributeValuesChanges(const QString &, const QgsChangedAttributesMap &)"),self.setProjectDirty2)
            QObject.disconnect(layer, SIGNAL("committedGeometriesChanges(const QString &, const QgsGeometryMap &)"),self.setProjectDirty2)

    def connectMemoryLayers( self ):
        for layer in self.memoryLayers():
            self.connectProvider( layer )

    def disconnectMemoryLayers( self ):
        for layer in self.memoryLayers():
            self.disconnectProvider( layer )


    def unload(self):      
        # self._iface.removePluginMenu("&Test tools",self._loadadjaction)
        pass

    def loadData(self):
        filename = self.memoryLayerFile()
        file = QFile(filename)
        if file.exists():
            layers = list(self.memoryLayers())
            if layers:
                try:
                    with Reader(filename) as reader:
                        reader.readLayers(layers)
                except:
                    QMessageBox.information(
                        self._iface.mainWindow(),"Error reloading memory layers",
                        str(sys.exc_info()[1]) ) 

    def saveData(self):
        try:
            filename = self.memoryLayerFile()
            try:
                file=QFile(finfo.filePath())
                file.remove()
            except:
                pass
            layers = list(self.memoryLayers())
            if layers:
                with Writer(filename) as writer:
                    writer.writeLayers( layers )
        except:
            QMessageBox.information(self._iface.mainWindow(),"Error saving memory layers",
                                    str(sys.exc_info()[1]) )

    def memoryLayers(self):
        for l in QgsMapLayerRegistry.instance().mapLayers().values():
            if self.isSavedLayer(l):
                yield l

    def isSavedLayer( self, l ):
        if l.type() != QgsMapLayer.VectorLayer:
            return
        pr = l.dataProvider()
        if not pr or pr.name() != 'memory':
            return False
        use = l.customProperty("SaveMemoryProvider")
        return use.isNull() or not use.toBool()

    def memoryLayerFile( self ):
        name = QgsProject.instance().fileName()
        if not name:
            return ''
        lname = name+".mldata"
        return lname

    def clearMemoryProvider(self, lyr):
        pl = lyr.dataProvider()
        pl.select()
        f = QgsFeature()
        while pl.nextFeature(f):
            pl.deleteFeatures(f.id())
        pl.deleteAttributes(pl.attributeIndexes())

    def setProjectDirty2(self,value1,value2):
        self.setProjectDirty()

    def setProjectDirty(self):
        QgsProject.instance().dirty(True)

    def showInfo(self):
        names = [str(l.name()) for l in self.memoryLayers()]
        message = ''
        if len(names) == 0:
            message = "This project contains no memory data provider layers to be saved"
        else:
            message = "The following memory data provider layers will be saved with this project:\n   "
            message += "\n   ".join(names)
        QMessageBox.information(self._iface.mainWindow(),"Memory layer saver info",message)
