import json

import requests
from PyQt5.QtWidgets import QAction, QInputDialog, QMessageBox

KDWS = "https://kystdatahuset.no/ws/"


class KystdatahusetAIS:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.session = None

    def initGui(self):
        self.action = QAction("Query AIS Positions", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        del self.action

    def login(self, username, password):
        auth_url = KDWS + "api/authorize"
        self.session = requests.Session()
        try:
            # Authenticate and get the access token
            auth_response = self.session.post(
                auth_url, json={"username": username, "password": password}
            )
            auth_response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            access_token = auth_response.json()["access_token"]
            # Include the access token in the headers for subsequent requests
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
            }
            self.session.headers.update(headers)
        except requests.exceptions.RequestException as e:
            QMessageBox.critical(None, "Error", f"Error authenticating: {e}")
        return self.session

    def run(self):
        api_url = KDWS + "/api/ais/positions/for-mmsis-time"
        username = "your_username"
        password = "your_password"
        session = self.login(username, password)

        try:
            # Prompt the user to enter the MMSI
            mmsi, ok = QInputDialog.getInt(None, "Enter MMSI", "Please enter the MMSI:")
            if not ok:
                return  # User canceled the input dialog

            # Prepare the data for the AIS positions query
            data = {
                "mmsiIds": [mmsi],
                "start": "201901011345",
                "end": "201901011345",
                # "minSpeed": 0.5,
            }

            # Query the AIS positions endpoint with the access token
            api_response = session.post(api_url, data=json.dumps(data))
            api_response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            result = api_response.json()
            QMessageBox.information(None, "AIS Positions", f"Response: {result}")
        except requests.exceptions.RequestException as e:
            QMessageBox.critical(None, "Error", f"Error querying AIS positions: {e}")
