from PyQt4.QtGui import *
from PyQt4.QtCore import *
from qgis.core import *
import sys

# import Resources

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
        self.loadMemoryLayers()

    def saveData(self):
        try:
            self.deleteMemoryDataFiles()
            self.saveMemoryLayers()
        except:
            QMessageBox.information(self._iface.mainWindow(),"Error saving memory layers",
                                    sys.exc_info[1] )

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


    def memoryLayerFile( self, layer ):
        name = QgsProject.instance().fileName()
        if not name:
            return ''
        id = layer.getLayerID()
        lname = name+".md_"+id+".gml"
        return lname

    def memoryLayerFiles( self ):
        proj = QFileInfo( QgsProject.instance().fileName() )
        projd = proj.absoluteDir()
        name = proj.fileName()
        if not name:
            return None
        filters = QStringList()
        filters.append(name+".md_*.gml")
        filters.append(name+".md_*.xsd")
        projd.setNameFilters(filters)
        return projd.entryInfoList()

    def saveMemoryLayers(self):
        # Create a gml file for each memory layer
        for lyr in self.memoryLayers():
            name = self.memoryLayerFile(lyr)
            if name:
                QgsVectorFileWriter.writeAsVectorFormat(lyr,name,"UTF-8",None,"GML")


    def loadMemoryLayers(self):
        for lyr in self.memoryLayers():
            name = self.memoryLayerFile(lyr)
            if QFileInfo(name).exists():
                self.loadMemoryLayer(lyr,name)

    def loadMemoryLayer( self, lyr, gmlFile ):
        settings = QSettings()
        prjSetting = settings.value("/Projections/defaultBehaviour")
        try:
            settings.setValue("/Projections/defaultBehaviour", QVariant(""))
            gml = QgsVectorLayer(gmlFile,"tmp","ogr")
        finally:
            if prjSetting:
                settings.setValue("/Projections/defaultBehaviour",prjSetting)
        if not gml or not gml.isValid():
            return
        self.clearMemoryProvider(lyr)
        pl = lyr.dataProvider()

        pg = gml.dataProvider()
        allAttrs = pg.attributeIndexes()
        fmap = pg.fields()
        copyAttrs = []
        for i in allAttrs:
            copyAttrs.append(i)
            pl.addAttributes([fmap[i]])

        pg.select(copyAttrs)
        f = QgsFeature()
        while pg.nextFeature(f):
            pl.addFeatures([f])
        # Fix for GDAL 1.9 GML driver, which creates fid as a new 
        # field when saving and reloading a GML based data set.
        if fmap[0].name() == 'fid':
            pl.deleteAttributes([0])
        if 'updateFieldMap' in dir(lyr):
            lyr.updateFieldMap()
        lyr.updateExtents()

            
    def clearMemoryProvider(self, lyr):
        pl = lyr.dataProvider()
        pl.select()
        f = QgsFeature()
        while pl.nextFeature(f):
            pl.deleteFeatures(f.id())
        pl.deleteAttributes(pl.attributeIndexes())

    def deleteMemoryDataFiles(self):
        # Delete existing memory layer data
        files = self.memoryLayerFiles()
        if not files: 
            return
        for finfo in files:
            file=QFile(finfo.filePath())
            file.remove()

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
