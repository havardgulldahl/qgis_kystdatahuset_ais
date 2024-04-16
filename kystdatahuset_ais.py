from collections import namedtuple
import json
import datetime

import requests

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsSettings,
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
POSITIONS_AREA = "https://kystdatahuset.no/ws/api/ais/positions/within-bbox-time"
POSITIONS_MMSI = "https://kystdatahuset.no/ws/api/ais/positions/for-mmsis-time"
TRACK_BY_MMSI_URL = "https://kystdatahuset.no/ws/api/tracks/for-ships/by-mmsi"
TRACK_BY_GEOM_URL = "https://kystdatahuset.no/ws/api/tracks/within-area"
LIVE_URL = "https://kystdatahuset.no/ws/api/ais/realtime/geojson"
SHIP_INFO_MMSI = "https://kystdatahuset.no/ws/api/ship/for-mmsis"
SHIP_INFO_FREETEXT = "https://kystdatahuset.no/ws/api/ship/free-text"
SHIP_INFO_IMO = "https://kystdatahuset.no/ws/api/ship/data/nsr/for-mmsis-imos"
LOCATION_FREETEXT = "https://kystdatahuset.no/ws/api/location/free-text"
SAILED_DISTANCE_MMSI = (
    "https://kystdatahuset.no/ws/api/tracks/sailed-distance/for-ships/by-mmsi"
)
LOCATION_ALL = "https://kystdatahuset.no/ws/api/location/all"
STATINFO = "https://kystdatahuset.no/ws/api/ais/statinfo/for-mmsis-time"
LOGIN = "https://kystdatahuset.no/ws/api/auth/login"
ARRIVALS_DEPARTURES = (
    "https://kystdatahuset.no/ws/api/voyage/arrivals-departures/for-location"
)
DEPARTURES = "https://kystdatahuset.no/ws/api/voyage/departures/for-locations"
ARRIVALS = "https://kystdatahuset.no/ws/api/voyage/arrivals/for-locations"

# named tuple to pythonly deal with position array/list that some methods return
Position = namedtuple(
    "Position",
    field_names="mmsi, date_time_utc, longitude, latitude, COG, SOG, ais_msg_type, calc_speed, sec_prevpoint, dist_prevpoint",
    module="kystdatahuset",
)


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

    def messagebar(self, message, error=False):
        self.iface.messageBar().pushMessage(
            "Kystdatahuset AIS:",
            message,
            level=Qgis.Info if not error else Qgis.Critical,
        )

    def _request(self, url, method="GET", data=None):
        try:
            response = self.session.request(method, url, json=data)
            response.raise_for_status()
            result = response.json()
            if not result["success"]:
                raise Exception(result["msg"])
            if result.get(
                "msg"
            ) is not None and "The operation has timed out." in result.get("msg"):
                raise Exception(f"The kystdatahuset request timed out on their end")
            if result.get("data") is None:
                raise Exception(f"kystdatahuset returned no data")
        except Exception as e:
            self.messagebar(f"Error querying AIS positions: {e}", error=True)
            raise
            return None
        return result.get("data")

    def lookup(self, mmsi: int) -> bool:
        "Lookup mmsi and get metadata (staticdata) if it exists, and put it in the db"
        endpoint = SHIP_INFO_MMSI
        data = {
            "MmsiIds": [
                mmsi,
            ]
        }
        resp = self._request(endpoint, data=data, method="POST")
        if len(resp) == 0:
            # no data found
            return False

        ship = resp.pop()  # get first record
        QgsMessageLog.logMessage(f"Found ship: {ship}")
        return ship

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
            QgsMessageLog.logMessage(
                f"Logged in successfully with token: {access_token[:10]}..."
            )
        except Exception as e:
            self.messagebar(f"Error authenticating: {e}", error=True)
        return self.session

    def renderTest(self, painter):
        # use painter for drawing to map canvas
        QgsMessageLog.logMessage("TestPlugin: renderTest called!")

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

            ship = self.lookup(mmsi)
            shipname = ship.get("shipname", "Unknown")
            # Prepare the data for the AIS positions query
            data = {
                "mmsiIds": [mmsi],
                "start": "201901011345",
                "end": "201901021345",
                # "minSpeed": 0.5,
            }

            QgsMessageLog.logMessage(
                f"Gathering AIS positions for MMSI {mmsi} / {shipname}"
            )
            # Query the AIS positions endpoint with the access token
            api_response = session.post(api_url, data=json.dumps(data))
            api_response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            result = api_response.json()
            if not result["success"]:
                raise Exception(result["msg"])
        except Exception as e:
            self.messagebar(f"Error querying AIS positions: {e}", error=True)
            return

        positions = result["data"]
        QgsMessageLog.logMessage(f"Received {len(positions)} positions for MMSI {mmsi}")
        # Create a memory layer to display the AIS positions
        uri = (
            "Point?crs=epsg:4326&"
            "field=name:string(30)&"
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
        vl = QgsVectorLayer(uri, f"AIS Positions for {mmsi}", "memory")
        vl.setCustomProperty("MMSI", mmsi)
        pr = vl.dataProvider()

        # Add fields to the layer
        pr.addAttributes(
            [
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
        """
            [
            258500000,
            "2019-01-02T00:00:02",
            21.7261,
            70.4006,
            115.3,
            15.1,
            3,
            40.8,
            1,
            21
            ],
        """
        # Iterate over the JSON data and add features to the layer
        for row in positions:
            try:
                pos = Position(*row)
            except Exception as e:
                QgsMessageLog.logMessage(f"Error creating Position: {e}")
                continue
            feature = QgsFeature()
            feature.setGeometry(
                QgsGeometry.fromPointXY(QgsPointXY(pos.longitude, pos.latitude))
            )
            feature.setAttributes(
                [
                    shipname,
                    pos.mmsi,
                    pos.date_time_utc,
                    pos.COG,
                    pos.SOG,
                    pos.ais_msg_type,
                    pos.calc_speed,
                    pos.sec_prevpoint,
                    pos.dist_prevpoint,
                ]
            )
            pr.addFeatures([feature])
            # QgsMessageLog.logMessage(f"Added feature for MMSI {pos.mmsi}")

        # Update the layer's extent
        vl.updateExtents()

        # Add the layer to the project
        QgsProject.instance().addMapLayer(vl)
        QgsMessageLog.logMessage("Layer added to project")
