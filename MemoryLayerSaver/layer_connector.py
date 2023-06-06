from qgis.core import QgsProject
from qgis.PyQt.QtCore import QObject


class LayerConnector(QObject):
    """Generic class to connect to all layers in the project"""

    def __init__(self, delay_connect=False):
        super().__init__()
        self.attached = False

        if not delay_connect:
            self.attach()

    def __del__(self):
        # Disconnect the signal
        self.detach()

    def attach(self):
        # Whenever a layer is added to the project, connect to it
        QgsProject.instance().layerWasAdded.connect(self.connect_layer)
        QgsProject.instance().layersWillBeRemoved.connect(self.disconnect_layers)
        # Connect to all layers already in the project
        self.connect_layers()
        self.attached = True

    def detach(self):
        """Detach from the project"""

        # If we are not attached, do nothing
        if not self.attached:
            return

        # Disconnect the signal
        QgsProject.instance().layerWasAdded.disconnect(self.connect_layer)
        QgsProject.instance().layersWillBeRemoved.disconnect(self.disconnect_layers)

        # Disconnect from all layers
        self.disconnect_layers()
        self.attached = False

    def connect_layers(self):
        """Connect to all the layers already in the project"""
        for layer in QgsProject.instance().mapLayers().values():
            self.connect_layer(layer)

    def disconnect_layers(self, layer_ids=None):
        """Disconnect from all the layers already in the project"""

        if not layer_ids:
            for layer in QgsProject.instance().mapLayers().values():
                self.disconnect_layer(layer)
        else:
            for layer_id in layer_ids:
                self.disconnect_layer(QgsProject.instance().mapLayers().get(layer_id))

    def connect_layer(self, layer):
        """This method should be overridden by the child class"""
        pass

    def disconnect_layer(self, layer):
        """This method should be overridden by the child class"""
        pass
