from qgis.core import QgsProject
from qgis.PyQt.QtCore import QObject


class LayerConnector(QObject):
    """Generic class to connect to all layers in the project"""

    def __init__(self):
        super().__init__()

        # Whenever a layer is added to the project, connect to it
        QgsProject.instance().layerWasAdded.connect(self.connect_layer)

        # Connect to all layers already in the project
        self.connect_layers()

    def __del__(self):
        # Disconnect the signal
        QgsProject.instance().layerWasAdded.disconnect(self.connect_layer)

        # Disconnect from all layers
        self.disconnect_layers()

    def connect_layers(self):
        """Connect to all the layers already in the project"""
        for layer in QgsProject.instance().mapLayers().values():
            self.connect_layer(layer)

    def disconnect_layers(self):
        """Disconnect from all the layers already in the project"""
        for layer in QgsProject.instance().mapLayers().values():
            self.disconnect_layer(layer)

    def connect_layer(self, layer):
        """This method should be overridden by the child class"""
        pass

    def disconnect_layer(self, layer):
        """This method should be overridden by the child class"""
        pass
