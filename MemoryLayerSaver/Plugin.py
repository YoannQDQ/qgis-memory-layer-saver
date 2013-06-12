from PyQt4.QtGui import *
from PyQt4.QtCore import *
from qgis.core import *
import sys

# import Resources

from MemoryLayerSaver import MemoryLayerSaver
import Resources

class Plugin:

    def __init__( self, iface ):
        self._iface = iface
        self._saver = MemoryLayerSaver(iface)

    def initGui(self):
        self._saver.attachToProject()
        self._infoAction = QAction(QIcon(":plugins/MemoryLayerSaver/plugin.png"),
                                   "Display memory layer information",
                                   self._iface.mainWindow())
        QObject.connect(self._infoAction,SIGNAL("triggered()"),self._saver.showInfo)
        self._iface.addPluginToMenu("&Memory layer saver", self._infoAction)

    def unload( self ):
        self._iface.removePluginMenu("&Memory layer saver",self._infoAction)
        self._saver.detachFromProject()
