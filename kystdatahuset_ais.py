import datetime
import json
from collections import namedtuple
from typing import List, Optional

import requests
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsMessageLog,
    QgsNetworkAccessManager,
    QgsPointXY,
    QgsProject,
    QgsRuleBasedRenderer,
    QgsSettings,
    QgsSymbol,
    QgsTask,
    QgsVectorLayer,
)
from qgis.gui import QgsOptionsPageWidget, QgsOptionsWidgetFactory
from qgis.PyQt.QtCore import (
    QCoreApplication,
    QDate,
    QEventLoop,
    QJsonDocument,
    QUrl,
    QVariant,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QAction,
    QDateTimeEdit,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolBar,
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


def dateformatter(dt: datetime) -> str:
    "Format dates the way kystdatahuset likes them: YYYYMMDDHHmm"
    return dt.strftime("%Y%m%d%H%M")


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


MESSAGE_CATEGORY = "KystdatahusetAIS"


class RequestTask(QgsTask):
    def __init__(self, description, url, nam, data=None, token=None, method="GET"):
        super().__init__(description, QgsTask.CanCancel)
        self.url = url
        self.data = data
        self.method = method
        self.token = token
        self.nam = nam
        self.result = None
        self.exception = None

    def run(self):
        QgsMessageLog.logMessage(
            'Started task "{}"'.format(self.description()), MESSAGE_CATEGORY, Qgis.Info
        )
        request = QNetworkRequest(QUrl(self.url))
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"application/json")
        if self.token is not None:
            request.setRawHeader(b"Authorization", f"Bearer {self.token}".encode())

        if self.method == "GET":
            reply = self.nam.blockingGet(request)
        elif self.method == "POST":
            json_data = QJsonDocument(self.data).toJson()
            reply = self.nam.blockingPost(request, json_data)
        else:
            self.exception = Exception(f"Unsupported HTTP method: {self.method}")
            return False

        while not reply.isFinished():
            if self.isCanceled():
                return False
            QgsApplication.processEvents()

        if reply.error() != QNetworkReply.NoError:
            self.exception = Exception(reply.errorString())
            return False

        result = json.loads(bytes(reply.content()))

        if not result["success"]:
            self.exception = Exception(result["msg"])
            return False

        if result.get(
            "msg"
        ) is not None and "The operation has timed out." in result.get("msg"):
            self.exception = Exception(
                "The kystdatahuset request timed out on their end"
            )
            return False

        if result.get("data") is None:
            self.exception = Exception("kystdatahuset returned no data")
            return False

        self.result = result.get("data")
        return True

    def finished(self, result):
        if result:
            QgsMessageLog.logMessage(
                f'Request task "{self.description()}" completed successfully',
                "RequestTask",
                Qgis.Success,
            )
        else:
            QgsMessageLog.logMessage(
                f'Request task "{self.description()}" failed with exception: {self.exception}',
                "RequestTask",
                Qgis.Critical,
            )
            raise self.exception

    def cancel(self):
        QgsMessageLog.logMessage(
            'RandomTask "{name}" was canceled'.format(name=self.description()),
            MESSAGE_CATEGORY,
            Qgis.Info,
        )
        super().cancel()


class KystdatahusetAIS:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.session = None
        self.token = None
        self.nam = QgsNetworkAccessManager()
        self.nam.setTimeout(15 * 1000)  # milliseconds

    def initGui(self):
        # Create a toolbar
        self.toolbar = QToolBar("Kystdatahuset AIS")
        self.toolbar.setObjectName("KystdatahusetAISToolbar")
        self.iface.addToolBar(self.toolbar)

        # Create a widget to hold the toolbar items
        self.toolbar_widget = QWidget()
        self.toolbar_layout = QHBoxLayout()
        self.toolbar_widget.setLayout(self.toolbar_layout)
        # Create a label for MMSI
        self.mmsi_label = QLabel("MMSI: ")
        self.toolbar_layout.addWidget(self.mmsi_label)

        # Create an integer spin box
        self.mmsi_spinbox = QSpinBox()
        settings = QgsSettings()
        last_mmsi = int(settings.value("KDWS/last_mmsi", 0))
        self.mmsi_spinbox.setValue(last_mmsi)
        self.mmsi_spinbox.setMinimumWidth(10)
        self.mmsi_spinbox.setRange(100_000_000, 999_999_999)
        self.toolbar_layout.addWidget(self.mmsi_spinbox)

        # Create two date spinners
        self.start_date_spinner = QDateTimeEdit()
        self.start_date_spinner.setDate(QDate.currentDate())
        self.start_date_spinner.setCalendarPopup(True)
        self.toolbar_layout.addWidget(self.start_date_spinner)

        self.end_date_spinner = QDateTimeEdit()
        self.end_date_spinner.setDate(QDate.currentDate())
        self.end_date_spinner.setCalendarPopup(True)
        self.toolbar_layout.addWidget(self.end_date_spinner)

        # Create a lookup button
        self.get_ais_button = QPushButton("Get AIS Positions")
        self.get_ais_button.clicked.connect(self.run)
        self.toolbar_layout.addWidget(self.get_ais_button)

        # Add the toolbar widget to the toolbar
        self.toolbar.addWidget(self.toolbar_widget)

        # Add the plugin to the menu
        self.iface.addPluginToMenu("Kystdatahuset", self.action)

        # Register the options widget factory
        self.options_factory = MyPluginOptionsFactory()
        self.options_factory.setTitle("Kystdatahuset AIS")
        self.iface.registerOptionsWidgetFactory(self.options_factory)

        # Connect to the renderComplete signal
        # self.iface.mapCanvas().renderComplete.connect(self.renderTest)

    def tr(self, message: str) -> str:
        return QCoreApplication.translate("KystdatahusetAIS", message)

    def unload(self):
        # Remove the toolbar
        self.toolbar.deleteLater()
        del self.toolbar

        # Remove the plugin from the menu
        self.iface.removePluginMenu("Kystdatahuset", self.action)
        del self.action

        # Unregister the options widget factory
        self.iface.unregisterOptionsWidgetFactory(self.options_factory)

        # Disconnect from the renderComplete signal of the canvas
        # self.iface.mapCanvas().renderComplete.disconnect(self.renderTest)

    def messagebar(self, message, error=False):
        self.iface.messageBar().pushMessage(
            "Kystdatahuset AIS:",
            message,
            level=Qgis.Info if not error else Qgis.Critical,
        )

    def _request_task(self, url, data=None, method="GET") -> list:
        task = RequestTask(f"Request to {url}", url, self.nam, self.token, data, method)
        QgsApplication.taskManager().addTask(task)
        return task.result

    def _request_blocking(self, url, data=None, method="GET") -> list:
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Authorization", f"Bearer {self.token}".encode())

        if method == "GET":
            reply = self.nam.blockingGet(request)
        elif method == "POST":
            json_data = QJsonDocument(data).toJson()
            reply = self.nam.blockingPost(request, json_data)
        else:
            raise Exception(f"Unsupported HTTP method: {method}")

        status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if status_code != 200:
            self.messagebar(f"Error querying AIS positions: {status_code}", error=True)
            raise Exception(f"Error querying AIS positions: {status_code}")

        if reply.error() != QNetworkReply.NoError:
            error_msg = reply.errorString()
            self.messagebar(f"Error querying AIS positions: {error_msg}", error=True)
            raise Exception(error_msg)

        result = json.loads(bytes(reply.content()))

        if not result["success"]:
            raise Exception(result["msg"])

        if result.get(
            "msg"
        ) is not None and "The operation has timed out." in result.get("msg"):
            raise Exception("The kystdatahuset request timed out on their end")

        if result.get("data") is None:
            raise Exception("kystdatahuset returned no data")

        return result.get("data")

    def _request_py(self, url, data=None, method="GET") -> list:
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

    _request = _request_py

    def lookup(self, mmsi: int) -> dict:
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

    def get_positions(
        self,
        mmsi: int,
        fromDate: datetime.datetime,
        toDate: datetime.datetime,
    ) -> List[Position]:
        # historic url for a vessel, with timespan
        endpoint = POSITIONS_MMSI
        data = {
            "MmsiIds": [mmsi],
            "Start": dateformatter(fromDate),  # type: ignore
            "End": dateformatter(toDate),  # type: ignore
        }
        response = self._request(endpoint, data, method="POST")
        return [Position(*row) for row in response]

    def login(self, username, password):
        auth_url = LOGIN
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
            self.token = access_token
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

    # def renderTest(self, painter):
    ## use painter for drawing to map canvas
    # QgsMessageLog.logMessage("TestPlugin: renderTest called!")

    def run(self):
        # Get the values from the integer spin box and date spinners
        mmsi: int = self.mmsi_spinbox.value()
        start_date = self.start_date_spinner.dateTime().toPyDateTime()
        end_date = self.end_date_spinner.dateTime().toPyDateTime()

        # check if start_date is before end_date
        if start_date > end_date:
            QMessageBox.critical(
                None,
                "Error",
                "The start date must be before the end date",
                QMessageBox.Ok,
            )
            return

        settings = QgsSettings()
        settings.setValue("KDWS/last_mmsi", mmsi)
        username = settings.value("KDWS/username", "")
        password = settings.value("KDWS/password", "")
        if self.session is None:
            self.login(username, password)

        # get ship info
        try:
            ship = self.lookup(mmsi)
            shipname = ship.get("shipname", None)
        except AttributeError:
            ship = None
            shipname = "Unknown"
        QgsMessageLog.logMessage(
            f"Gathering AIS positions for MMSI {mmsi} / {shipname or 'Unknown'}"
        )
        try:
            interval_start = start_date
            while interval_start < end_date:
                interval_end = min(
                    interval_start + datetime.timedelta(days=6), end_date
                )
                positions = self.get_positions(mmsi, interval_start, interval_end)
                self.messagebar(
                    f"Received {len(positions)} AIS positions for {shipname or mmsi}"
                )
                self.add_layer(mmsi, ship, positions)
                interval_start = interval_end

        except Exception as e:
            self.messagebar(f"Error querying AIS positions: {e}", error=True)
            return

    def add_layer(self, mmsi: int, ship: dict, positions: List[Position]):
        # Find an existing layer with the same MMSI or create a new one
        vl = pr = None
        shipname = ship.get("shipname", None)
        for layer in QgsProject.instance().mapLayers().values():
            if layer.customProperty("MMSI") == mmsi:
                QgsMessageLog.logMessage("Layer already exists")
                vl = layer
                pr = vl.dataProvider()
                break

        if vl is None:
            # Create a memory layer to display the AIS positions
            uri = (
                "Point?crs=epsg:4326&"
                "field=name:string(30)&"
                "field=mmsi:integer&"
                "field=datetime_utc:datetime&"
                "field=course:double&"
                "field=speed:double&"
                "field=AIS_message_number:integer&"
                "field=calc_speed:double&"
                "field=seconds_prev_point:double&"
                "field=distance_prev_point:double&"
                "index=yes"
            )
            vl = QgsVectorLayer(uri, f"AIS Positions for {shipname or mmsi}", "memory")
            vl.setCustomProperty("MMSI", mmsi)
            vl.setCustomProperty("shipname", shipname)
            vl.setCustomProperty("flag", ship.get("threecharcode", "Unknown"))
            vl.setCustomProperty("imo", ship.get("imono", "Unknown"))
            vl.setCustomProperty("callsign", ship.get("callsign", "Unknown"))
            vl.setCustomProperty("shiptype", ship.get("shiptypegroupnor", "Unknown"))

            pr = vl.dataProvider()

            # Add fields to the layer
            pr.addAttributes(
                [
                    QgsField("name", QVariant.String),
                    QgsField("mmsi", QVariant.Int),
                    QgsField("datetime_utc", QVariant.DateTime),
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
        for pos in positions:
            feature = QgsFeature()
            feature.setGeometry(
                QgsGeometry.fromPointXY(QgsPointXY(pos.longitude, pos.latitude))
            )
            feature.setAttributes(
                [
                    ship.get("shipname", None),
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

        # Define the symbology rules
        rules = [
            {
                "filter": '"speed" < 2',
                "symbol": QgsMarkerSymbol.createSimple(
                    {"name": "circle", "color": "red", "size": "6"}
                ),
            },
            {
                "filter": '"speed" >= 2',
                "symbol": QgsMarkerSymbol.createSimple(
                    {"name": "circle", "color": "#339cff", "size": "3"}
                ),
            },
        ]

        # Create a rule-based renderer and assign the rules
        renderer = QgsRuleBasedRenderer(QgsSymbol.defaultSymbol(vl.geometryType()))
        root_rule = renderer.rootRule()

        for rule in rules:
            label = f"""Speed {"<" if rule["filter"].startswith('"speed" <') else ">="} 2 knots"""
            expression = rule["filter"]
            symbol = rule["symbol"]
            new_rule = root_rule.children()[0].clone()
            new_rule.setLabel(label)
            new_rule.setFilterExpression(expression)
            new_rule.setSymbol(symbol)
            root_rule.appendChild(new_rule)

        root_rule.removeChildAt(0)
        vl.setRenderer(renderer)

        # If the layer is not in the project, add the layer to the project
        if vl not in QgsProject.instance().mapLayers().values():
            QgsProject.instance().addMapLayer(vl)
            QgsMessageLog.logMessage("Layer added to project")
