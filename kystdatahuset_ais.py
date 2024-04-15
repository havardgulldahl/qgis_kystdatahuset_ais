import json

import requests
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsOptionsPageWidget, QgsOptionsWidgetFactory
from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QHBoxLayout, QInputDialog, QMessageBox

KDWS = "https://kystdatahuset.no/ws/"


class MyPluginOptionsFactory(QgsOptionsWidgetFactory):

    def __init__(self):
        super().__init__()

    def icon(self):
        return QIcon("icon.png")

    def createWidget(self, parent):
        return ConfigOptionsPage(parent)


class ConfigOptionsPage(QgsOptionsPageWidget):

    def __init__(self, parent):
        super().__init__(parent)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)


class KystdatahusetAIS:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.session = None

    def initGui(self):
        self.action = QAction("Get Kystdatahuset AIS", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("Kystdatahuset", self.action)
        self.options_factory = MyPluginOptionsFactory()
        self.options_factory.setTitle("Kystdatahuset AIS")
        self.iface.registerOptionsWidgetFactory(self.options_factory)

        # connect to signal renderComplete which is emitted when canvas
        # rendering is done
        self.iface.mapCanvas().renderComplete.connect(self.renderTest)

    def tr(self, message):
        return QCoreApplication.translate("KystdatahusetAIS", message)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("Kystdatahuset", self.action)
        del self.action
        self.iface.unregisterOptionsWidgetFactory(self.options_factory)
        # disconnect form signal of the canvas
        self.iface.mapCanvas().renderComplete.disconnect(self.renderTest)

    def login(self, username, password):
        auth_url = KDWS + "api/auth/login"
        self.session = requests.Session()
        try:
            # Authenticate and get the access token
            auth_response = self.session.post(
                auth_url, json={"username": username, "password": password}
            )
            auth_response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            result = auth_response.json()
            if not result["success"]:
                raise Exception(result["msg"])
            access_token = result["data"]["JWT"]
            # Include the access token in the headers for subsequent requests
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
            }
            self.session.headers.update(headers)
            QMessageBox.information(
                None,
                "Success",
                f"Logged in successfully with token: {access_token[:10]}...",
            )
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error authenticating: {e}")
        return self.session

    def renderTest(self, painter):
        # use painter for drawing to map canvas
        print("TestPlugin: renderTest called!")

    def run(self):
        api_url = KDWS + "api/ais/positions/for-mmsis-time"
        username = "asdf"
        password = "asdf"
        if self.session is None:
            session = self.login(username, password)
        else:
            session = self.session

        try:
            # Prompt the user to enter the MMSI
            mmsi, ok = QInputDialog.getInt(None, "Enter MMSI", "Please enter the MMSI:")
            if not ok:
                return  # User canceled the input dialog

            # Prepare the data for the AIS positions query
            data = {
                "mmsiIds": [mmsi],
                "start": "201901011345",
                "end": "201902011345",
                # "minSpeed": 0.5,
            }

            # Query the AIS positions endpoint with the access token
            api_response = session.post(api_url, data=json.dumps(data))
            api_response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            result = api_response.json()
            QMessageBox.information(None, "AIS Positions", f"Response: {len(result)}")
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error querying AIS positions: {e}")
            return

        # Create a memory layer to display the AIS positions
        vl = QgsVectorLayer("Point", "API Points", "memory")
        pr = vl.dataProvider()

        # Add fields to the layer
        pr.addAttributes(
            [
                QgsField("id", QVariant.Int),
                QgsField("name", QVariant.String),
                QgsField("value", QVariant.Double),
            ]
        )
        vl.updateFields()

        # Iterate over the JSON data and add features to the layer
        for row in result:
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(row["x"], row["y"])))
            feature.setAttributes([row["id"], row["name"], row["value"]])
            pr.addFeatures([feature])

        # Update the layer's extent
        vl.updateExtents()

        # Add the layer to the project
        QgsProject.instance().addMapLayer(vl)
