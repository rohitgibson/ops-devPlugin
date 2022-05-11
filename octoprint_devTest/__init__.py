import os #allows (1) use of 127.0.0.1 for dev purposes && (2) integration of command line functions for future admin controls (to initiate OctoPrint updates, restarts, or shutdowns from the dashboard)
from threading import Event, Thread
import requests #essential for pushing data to server
import socket 
import multiprocessing

import hashlib
import secrets
import hmac

from octoprint import events
from octoprint.plugin import StartupPlugin, ShutdownPlugin, SettingsPlugin, TemplatePlugin, SimpleApiPlugin, EventHandlerPlugin
from octoprint.events import Events
import octoprint.printer
from octoprint.util import RepeatedTimer
from requests.models import InvalidURL

from . import plugin_config


class _connection_instance(RepeatedTimer):
    def __init__(self, server_endpoint, printer_id, access_key, current_ip):
        self._server_endpoint = "http://"+server_endpoint+"/printers/status/BB477-7"
        self._printer_uuid = printer_id
        self._server_api_key = access_key
        self._currentIP = current_ip
    
        self._session_context = None
        self._session_prepped = None
        self._session_endpoint = ""
        self._session_headers = {} #Unsure if this will be replaced with auth (will remove comment in future version)
        
        self._connection_response = None
        self._url_invalid = None

    def _connection_init(self):
        __plugin_implementation__._logger.info("OCTOPRINTSERVER -- TESTING CONNECTION")

        #"NO_PROXY" only needed if connecting to localhost (for development purposes); brings in "connection_state" variable for modification
        os.environ['NO_PROXY'] = '127.0.0.1'
        connection_state = __plugin_implementation__._connection_state
        
        #Fundamental variables for session creation/context 
        self._session_endpoint = self._server_endpoint
        session_data = {"label":self._printer_uuid, "index_key":"2", "current_ip":self._currentIP, "msg_body":"OctoPrint active"}
        self._session_headers = {} #nOTE -> headers will be added with server-side pairing/api logic -- Basic HTTP Auth; format is "Server-API-Key":<API Key/password>
        
        #Creates "prepped" request
        try:
            __plugin_implementation__._logger.info("OCTOPRINTSERVER -- Testing URL validity")
            session_init = requests.Request('POST', self._session_endpoint, headers=self._session_headers, data=session_data)
            session_prepped = session_init.prepare()
            self._url_invalid = False
        except InvalidURL:
            __plugin_implementation__._logger.info("Unable to connect -- URL invalid")
            self._url_invalid = True

        self._session_context = requests.Session()
        #Attempts connection with session context; only runs if URL is valid
        if self._url_invalid is False:
            try:
                connection_attempt = self._session_context.send(session_prepped) 
                self._connection_response = connection_attempt.status_code
                __plugin_implementation__._logger.info(self._connection_response)
            except Exception:
                __plugin_implementation__._logger.info("Unable to connect -- URL invalid")
                self._connection_response = 0
                connection_state = 2
        else:
            __plugin_implementation__._logger.info("Unable to connect -- URL invalid")
            pass
        
        #IF connection successful --> starts connection proper
        if self._connection_response == 200:   
            connection_state = 3
            __plugin_implementation__._logger.info("CONNECTION ESTABLISHED; PLUGIN ACTIVE")
            
            #timed "server alive" message start
            __plugin_implementation__._main_timer = RepeatedTimer(60, self._connection_keep, run_first=True, condition=__plugin_implementation__._check_timer)
            __plugin_implementation__._main_timer.start()
            
            #"Connection Instance" session details updated
            self._session_prepped = self._session_context.prepare_request(requests.Request('POST', self._session_endpoint, headers=self._session_headers))
        else:
            connection_state = 2
            __plugin_implementation__._logger.info("CONNECTION FAILED -- Cannot connect to server")

        #Reports "connection_state" output to main __plugin_implementation__ object
        __plugin_implementation__._connection_state = connection_state

    def _connection_keep(self):
        if __plugin_implementation__._connection_state != 1:
            self._connection_fire(self._printer_uuid,'Server alive')
        else:
            pass
    
    #Relies on existing persistent HTTP "session" -- pushes message to external API when called
    def _connection_fire(self, origin, message):
        output = {'origin':origin,'message':message}
        if __plugin_implementation__._connection_state != 1:
            try:
                self._session_context.post(url=self._session_prepped.url, data=output)
                __plugin_implementation__._logger.info("_connection_event -- POST success")
            except Exception:
                __plugin_implementation__._logger.info("_connection_event -- POST failed")
        else: 
            pass

class octoprintServer(     
    StartupPlugin,     
    ShutdownPlugin,     
    EventHandlerPlugin,     
    SettingsPlugin,     
    TemplatePlugin,     
    SimpleApiPlugin   
    ):    
    #currentIP = "http://" + str(socket.gethostbyname(socket.gethostname())) + ":80"

    def __init__(self):
        self._server_endpoint = plugin_config.SERVER_ENDPOINT
        self._printer_id = plugin_config.PRINTER_ID
        self._access_key = plugin_config.ACCESS_KEY

        #Server connection variables
        self._currentIP = "http://" + str(socket.gethostbyname(socket.gethostname())) + ":80" #Need to test whether this works on OctoPrint (no reason it shouldn't)
        self._connection_state = 2 #1 = connection terminated; 2 = not connected; 3 = connection alive
        self._main_timer = None
        self._operation_state = False #True if "OPERATIONAL" event fired

    def data_validation(self):
        self._logger.info("Validating config...")
        if plugin_config.SERVER_ENDPOINT is None or plugin_config.PRINTER_ID is None or plugin_config.ACCESS_KEY is None:
            self._logger.info("Config invalid -- missing one or more values")
            #Stops connection with existing config
            #hash_and_send("Config invalid -- missing one or more values", "/api/printers/")
        else:
            self._logger.info("Config valid -- starting plugin")
            #Attempts connection with existing config
            self._server_endpoint = plugin_config.SERVER_ENDPOINT; self._printer_id = plugin_config.PRINTER_ID; self._access_key = plugin_config.ACCESS_KEY
            self.plugin_init()

    def plugin_init(self):
        self._logger.info("Connection attempt started")
        
        if self._connection_state == 3:
            self._connection_kill(1)
        else:
            self._logger.info(f"Connecting to {self._server_endpoint}")
            self._connection = _connection_instance(self._server_endpoint, self._printer_id, self._access_key, self._currentIP)
            _test_process = multiprocessing.Process(target=self._connection._connection_init())
            _test_process.start()
            _test_process_two = multiprocessing.Process(target=self._connection._connection_init())
            _test_process_two.start()

    def on_after_startup(self):
        self._logger.info("Plugin started")
        self.data_validation()

    def get_api_commands(self):
        return dict(
            editconfig=["server_endpoint","printer_uuid","access_key"],
            pair=[]
        )

    def on_api_command(self, command, data):
        import json
        import flask
        if command == "editconfig":
            self._ping_data = data
            
            plugin_config.SERVER_ENDPOINT = self._ping_data["server_endpoint"]
            plugin_config.PRINTER_ID = self._ping_data["printer_uuid"]
            plugin_config.ACCESS_KEY = self._ping_data["access_key"]

            self.data_validation
        elif command == "pair":
            pass
        else:
            self._logger.info("Failed external command received")  

        repr(plugin_config)

    def _check_timer(self):
        #Whether timer is active (inactive if connection terminated)
        if self._connection_state != 1:
            return True
        else:
            return False

    """
    def on_event(self, event, payload):
        event = event
        payload = payload
        try:
            if event in (Events.CONNECTED, Events.DISCONNECTED, Events.CLIENT_OPENED, Events.CLIENT_CLOSED, Events.PRINTER_STATE_CHANGED):
                __plugin_implementation__._connection._connection_fire(event,payload)

                #"error" event reporting
            elif event in (Events.ERROR):
                __plugin_implementation__._connection._connection_fire(event,payload)
            else:
                pass
        except AttributeError:
            pass
    """

    def _connection_kill(self, reason):
        if reason == 1: #Connection restart (due to change in settings or *manual reset)
            self._connection_state = 2
            self._logger.info("CONNECTION RESTARTING")
        elif reason == 2: #Connection terminated 
            self._connection_state = 1
            self._logger.info("CONNECTION TERMINATED")


__plugin_identifier__ = "devTest"
__plugin_package__ = "octoprint_devTest"
__plugin_name__ = "OPS-DevPlugin"
__plugin_version__ = "2022-05-D1"
__plugin_description__ = "Facilitates connections between OctoPrint instances and a paired cloud server."
__plugin_author__ = "Rohit Gibson"
__plugin_author_email__ = "rgibso50@students.kennesaw.edu"
__plugin_pythoncompat__ = ">=3,<4"
__plugin_implementation__ = octoprintServer()
__plugin_hooks__ = {}