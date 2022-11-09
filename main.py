import hashlib
import json
import logging
import os
import ssl
import threading
import uuid
from datetime import datetime, timedelta
from time import sleep
from urllib.parse import urlparse

import boto3
import requests
from botocore.exceptions import EndpointConnectionError
from dotenv import dotenv_values
from paho.mqtt.client import Client
from pycognito.aws_srp import AWSSRP
from pycognito.utils import RequestsSrpAuth, TokenType

import constants
from dotdict import dotdict


class MqttConnection:

    def __init__(self, beenera_connection, upstream_mqtt_config):
        self.beenera_connection = beenera_connection
        self.mqtt_client_id = str(uuid.uuid4())
        self.mqtt_client = Client(client_id=self.mqtt_client_id, transport="websockets", reconnect_on_failure=False)
        self.mqtt_client.tls_set_context(ssl.create_default_context())
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_subscribe = self.on_subscribe

        self.server_uri = None
        self.devices = []

        self.upstream_mqtt = UpstreamMqttConnection(upstream_mqtt_config)

        self.subscribe_requests = {}
        self.device_mapping = {}

        self.logger = logger.getChild(self.__class__.__name__)

        self.last_received_timestamp = datetime.min
        self.timeout_checker = threading.Thread(target=self.check_timeout, daemon=True)

    def connect(self):
        self.update_data()
        self.upstream_mqtt.connect()
        self.logger.info(f"Beenera MQTT Client connecting to {self.server_uri}")
        parsed_uri = urlparse(self.server_uri)
        host = parsed_uri.hostname
        port = parsed_uri.port or 443
        path = parsed_uri.path + '?' + parsed_uri.query
        # required, because the Signature for AWS spans the host header _without_ a port number; Paho MQTT would add the port
        headers = {"Host": host}
        self.mqtt_client.ws_set_options(path=path, headers=headers)
        self.mqtt_client.connect(host=host, port=port)
        if not self.timeout_checker.is_alive():
            self.timeout_checker.start()

    def check_timeout(self):
        self.logger.debug("Check for timeout has started.")
        while True:
            if self.mqtt_client.is_connected() and (datetime.now() - self.last_received_timestamp).total_seconds() > 30:
                self.logger.info("Did not receive an update for 30 seconds. Reconnecting...")
                self.mqtt_client.disconnect()
            sleep(5)

    def on_connect(self, client, userdata, flags, rc):
        self.logger.info(
            f"Beenera MQTT Client successfully connected. Is reconnect: {rc}, subscribing {len(self.devices)} devices")
        for device in self.devices:
            device_sha256 = hashlib.sha256(device['ID'].encode('utf-8'))
            topic = device_sha256.hexdigest()
            result, mid = client.subscribe(topic=topic)
            self.subscribe_requests[mid] = topic
            self.device_mapping[topic] = device

    def on_connect_fail(self, client, userdata):
        self.logger.info(f"Client failed to connect, retrying in 5 seconds.")
        sleep(5)
        self.connect()

    def on_disconnect(self, client, userdata, rc):
        self.logger.debug(f"Client disconnected. rc: {rc}")
        self.connect()

    def on_subscribe(self, client, userdata, mid, granted_qos):
        self.logger.info(f"MQTT Client subscribed to topic {self.subscribe_requests[mid]}")

    def on_message(self, client, userdata, message):
        self.logger.debug(f"MQTT Message received on topic {message.topic}")
        self.logger.debug({message.payload})
        self.upstream_mqtt.publish(device=self.device_mapping[message.topic], message=message.payload)
        self.last_received_timestamp = datetime.now()

    def update_data(self):
        account_data = {}
        while account_data == {}:
            account_data = self.beenera_connection.get_account_data()
            if account_data == {}:
                self.logger.info(f"Could not get account data. Retrying in 5s.")
                sleep(5)

        self.server_uri = account_data['LiveURI']
        self.devices = account_data['Devices']


class UpstreamMqttConnection:
    def __init__(self, config):
        self.host = config.MQTT_HOST
        self.port = config.MQTT_PORT or 1883
        self.username = config.MQTT_USER or None
        self.password = config.MQTT_PASSWORD or None
        self.topic_base = config.MQTT_TOPIC_BASE
        self.mqtt_client = Client(client_id=str(uuid.uuid4()), reconnect_on_failure=True)
        self.mqtt_client.username_pw_set(self.username, self.password)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_disconnect = self.on_disconnect
        self.logger = logger.getChild(self.__class__.__name__)

        self.seen_topics = []

    def connect(self):
        self.logger.info(f"Upstream MQTT Client connecting to {self.host}:{self.port}")
        self.mqtt_client.connect(host=self.host, port=self.port)
        self.mqtt_client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        self.logger.info(f"Upstream MQTT Client connected. Is reconnect: {rc}")

    def on_disconnect(self, client, userdata, rc):
        self.logger.info(f"Client disconnected. Trying to reconnect.")

    def publish(self, device, message):
        topic = self.topic_base + '/' + device['MeterID']
        if topic not in self.seen_topics:
            self.logger.info(f"First time publishing data to topic '{topic}'")
            self.seen_topics.append(topic)

        self.mqtt_client.publish(topic, message)


class BeeneraConnection:
    def __init__(self, username, password):
        self.username = username
        self.password = password

        self.tokens = None
        self.live_uri = None
        self.devices = None

        self.cognito_client = boto3.client('cognito-idp', region_name=constants.COGNITO_POOL_REGION)

        self.logger = logger.getChild(self.__class__.__name__)

    def auth(self):
        result = RequestsSrpAuth(
            username=self.username,
            password=self.password,
            user_pool_id=constants.COGNITO_POOL_ID,
            client_id=constants.COGNITO_CLIENT_ID,
            user_pool_region=constants.COGNITO_POOL_REGION,
            auth_token_type=TokenType.ID_TOKEN
        )

        return result

    def get_account_data(self):
        self.logger.info(f"Retrieving account data for {self.username}")
        req = requests.Request('GET', constants.ENERA_ACCOUNT_URL,
                               headers={"X-Mandant": constants.ENERA_MANDANT, "Platform": "android"}, auth=self.auth())
        try:
            prepared = req.prepare()
            s = requests.Session()
            r = s.send(prepared)
            data = json.loads(r.text)
            self.devices = data["Devices"]
            self.live_uri = data["LiveURI"]
            return data
        except EndpointConnectionError as e:
            self.logger.info(f"Could not authenticate. Error message: {str(e)}")
        except self.cognito_client.exceptions.UserNotFoundException as e:
            self.logger.info(f"User not found. Please check configuration. Error message: {str(e)}")
            exit(1)
        except self.cognito_client.exceptions.NotAuthorizedException as e:
            self.logger.info(f"Password incorrect. Please check configuration. Error message: {str(e)}")
            exit(1)
        except Exception as e:
            self.logger.info(f"Unexpected error during retrieval of account data: {str(e)}")

        return {}

    def login(self):
        aws = AWSSRP(
            username=self.username,
            password=self.password,
            pool_id=constants.COGNITO_POOL_ID,
            client_id=constants.COGNITO_CLIENT_ID,
            client=self.cognito_client
        )
        tokens = aws.authenticate_user()

        self.tokens = tokens['AuthenticationResult']
        self.tokens['ExpirationDateTime'] = datetime.now() + timedelta(seconds=self.tokens['ExpiresIn'])

        self.logger.debug(tokens)


config = dotdict({})
logger = logging.getLogger(__name__)


def validate_config():
    has_error = False
    if config.MQTT_HOST is None:
        logger.error("MQTT_HOST needs to be defined!")
        has_error = True
    if config.BEENERA_USER is None:
        logger.error("BEENERA_USER needs to be defined!")
        has_error = True
    if config.BEENERA_PASSWORD is None:
        logger.error("BEENERA_PASSWORD needs to be defined!")
        has_error = True

    if has_error:
        exit(1)

    if config.MQTT_TOPIC_BASE is None:
        config.MQTT_TOPIC_BASE = "power"

    config.MQTT_PORT = int(config.MQTT_PORT or "1883")


def main():
    global config, logger

    config = dotdict({
        **dotenv_values(".env"),
        **os.environ
    })

    logging.basicConfig(format='%(asctime)s | %(name)s | %(levelname)s | %(message)s')
    logger.setLevel(config.LOGLEVEL or "INFO")

    validate_config()

    conn = BeeneraConnection(username=config.BEENERA_USER, password=config.BEENERA_PASSWORD)
    mqtt_conn = MqttConnection(conn, upstream_mqtt_config=config)

    try:
        while True:
            # loop_forever() exits when disconnecting (when not reconnecting automatically)
            # auto reconnect cannot be used, as we need to refresh our credentials before reconnecting
            mqtt_conn.connect()
            mqtt_conn.mqtt_client.loop_forever()
            logger.info("Lost connection, reconnecting...")
    except KeyboardInterrupt:
        logger.info("Interrupted, exiting.")
    os._exit(1)


if __name__ == '__main__':
    main()
