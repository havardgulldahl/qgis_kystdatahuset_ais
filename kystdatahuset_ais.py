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
from qgis.PyQt.QtWidgets import (
    QAction,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

KDWS = "https://kystdatahuset.no/ws/"


class MyPluginOptionsFactory(QgsOptionsWidgetFactory):

    def __init__(self):
        super().__init__()

    def icon(self):
        return QIcon("icon.png")

    def createWidget(self, parent):
        return ConfigOptionsPage(parent)


from qgis.PyQt.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit
from qgis.core import QgsSettings, QgsLogger


class ConfigOptionsPage(QgsOptionsPageWidget):
    def __init__(self, parent):
        super().__init__(parent)

        # Create the main layout
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(main_layout)

        # Create a widget to hold the username and password fields
        credentials_widget = QWidget()
        credentials_layout = QVBoxLayout()
        credentials_widget.setLayout(credentials_layout)

        # Username field
        username_layout = QHBoxLayout()
        username_label = QLabel("Username:")
        self.username_input = QLineEdit()
        username_layout.addWidget(username_label)
        username_layout.addWidget(self.username_input)
        credentials_layout.addLayout(username_layout)

        # Password field
        password_layout = QHBoxLayout()
        password_label = QLabel("Password:")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        password_layout.addWidget(password_label)
        password_layout.addWidget(self.password_input)
        credentials_layout.addLayout(password_layout)

        # Add the credentials widget to the main layout
        main_layout.addWidget(credentials_widget)

        # Load saved username and password
        self.load_credentials()

    def apply(self):
        # Save the username and password
        self.save_credentials()

    def save_credentials(self):
        settings = QgsSettings()
        settings.setValue("KDWS/username", self.username_input.text())
        settings.setValue("KDWS/password", self.password_input.text())

    def load_credentials(self):
        settings = QgsSettings()
        username = settings.value("KDWS/username", "")
        password = settings.value("KDWS/password", "")
        self.username_input.setText(username)
        self.password_input.setText(password)


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
            QgsLogger.debug(
                f"Logged in successfully with token: {access_token[:10]}..."
            )
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error authenticating: {e}")
        return self.session

    def renderTest(self, painter):
        # use painter for drawing to map canvas
        QgsLogger.debug("TestPlugin: renderTest called!")

    def run(self):
        settings = QgsSettings()
        username = settings.value("KDWS/username", "")
        password = settings.value("KDWS/password", "")
        if self.session is None:
            session = self.login(username, password)
        else:
            session = self.session

        api_url = KDWS + "api/ais/positions/for-mmsis-time"
        settings = QgsSettings()
        last_mmsi = settings.value("KDWS/last_mmsi", 0)
        try:
            # Prompt the user to enter the MMSI
            mmsi, ok = QInputDialog.getInt(
                None, "Enter MMSI", "Please enter the MMSI:", last_mmsi
            )
            if not ok:
                return  # User canceled the input dialog

            settings.setValue("KDWS/last_mmsi", mmsi)
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
            if not result["success"]:
                raise Exception(result["msg"])
        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error querying AIS positions: {e}")
            return

        positions = result["data"]
        QgsLogger.debug(f"Received {len(positions)} positions for MMSI {mmsi}")
        # Create a memory layer to display the AIS positions
        uri = (
            "Point?crs=epsg:4326&"
            "field=id:integer&"
            "field=name:string(20)&"
            "field=mmsi:integer&"
            "field=datetime_utc:date&"
            "field=course:double&"
            "field=speed:double&"
            "field=AIS_message_number:integer&"
            "field=calc_speed:double&"
            "field=seconds_prev_point:double&"
            "field=distance_prev_point:double&"
            "index=yes"
        )
        vl = QgsVectorLayer(uri, "API Points", "memory")
        vl.setCustomProperty("MMSI", mmsi)
        pr = vl.dataProvider()

        # Add fields to the layer
        pr.addAttributes(
            [
                QgsField("id", QVariant.Int),
                QgsField("name", QVariant.String),
                QgsField("mmsi", QVariant.Int),
                QgsField("datetime_utc", QVariant.Date),
                QgsField("course", QVariant.Double),
                QgsField("speed", QVariant.Double),
                QgsField("AIS_message_number", QVariant.Int),
                QgsField("calc_speed", QVariant.Double),
                QgsField("seconds_prev_point", QVariant.Double),
                QgsField("distance_prev_point", QVariant.Double),
            ]
        )
        vl.updateFields()

        # Iterate over the JSON data and add features to the layer
        for row in result:
            QgsLogger.debug(row)
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(row["x"], row["y"])))
            feature.setAttributes([row["id"], row["name"], row["value"]])
            pr.addFeatures([feature])

        # Update the layer's extent
        vl.updateExtents()

        # Add the layer to the project
        QgsProject.instance().addMapLayer(vl)
        QgsLogger.debug("Layer added to project")
