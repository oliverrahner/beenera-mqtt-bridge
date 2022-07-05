# This script connects to an upstream Beenera (https://beenera.de) instance
# and relays the incoming data to another MQTT broker
# The intention is to make the data available, amongst others, to home automation systems like
# OpenHAB, Home Assistant, FHEM or iobroker
import hashlib
import logging
import config
import constants
from paho.mqtt.client import Client
from pycognito.aws_srp import AWSSRP
import boto3
import uuid
import json
from datetime import datetime, timedelta
import requests
from pycognito.utils import RequestsSrpAuth, TokenType
from urllib.parse import urlparse, parse_qs, parse_qsl
import ssl

logging.basicConfig(format='%(asctime)s | %(name)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(config.LOGLEVEL or "INFO")

class MqttConnection:
    def __init__(self, beenera_connection=None, server_uri=None, devices=None, upstream_mqtt_config=None):
        if beenera_connection is not None and (server_uri is not None or devices is not None):
            raise Exception("Only beenera_connection OR server_uri and devices must be present.")
        if beenera_connection is None and (server_uri is None or devices is None):
            raise Exception("beenera_connection or server_uri AND devices must be present.")

        if beenera_connection is not None:
            server_uri = beenera_connection.live_uri
            devices = beenera_connection.devices

        self.mqtt_client_id = str(uuid.uuid4())
        self.mqtt_client = Client(client_id=self.mqtt_client_id, transport="websockets")
        self.mqtt_client.tls_set_context(ssl.create_default_context())
        self.server_uri = server_uri
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_subscribe = self.on_subscribe
        self.devices = devices

        self.upstream_mqtt = UpstreamMqttConnection(upstream_mqtt_config)

        self.subscribe_requests = {}
        self.device_mapping = {}

        self.logger = logger.getChild(self.__class__.__name__)
        pass

    def connect(self):
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

    def on_connect(self, client, userdata, flags, rc):
        self.logger.info(f"Beenera MQTT Client successfully connected. Is reconnect: {rc}, subscribing {len(self.devices)} devices")
        for device in self.devices:
            device_sha256 = hashlib.sha256(device['ID'].encode('utf-8'))
            topic = device_sha256.hexdigest()
            result, mid = client.subscribe(topic=topic)
            self.subscribe_requests[mid] = topic
            self.device_mapping[topic] = device

    def on_subscribe(self, client, userdata, mid, granted_qos):
        self.logger.info(f"MQTT Client subscribed to topic {self.subscribe_requests[mid]}")

    def on_message(self, client, userdata, message):
        self.logger.debug(f"MQTT Message received on topic {message.topic}")
        self.logger.debug({message.payload})
        self.upstream_mqtt.publish(device=self.device_mapping[message.topic], message=message.payload)


class UpstreamMqttConnection:
    def __init__(self, config):
        self.host = config['host']
        self.port = config['port']
        self.topic_base = config['topic_base']
        self.mqtt_client = Client(client_id=str(uuid.uuid4()))
        self.mqtt_client.on_connect = self.on_connect
        self.logger = logger.getChild(self.__class__.__name__)

        self.seen_topics = []

    def connect(self):
        self.logger.info(f"Upstream MQTT Client connecting to {self.host}:{self.port}")
        self.mqtt_client.connect(host=self.host, port=self.port)
        self.mqtt_client.loop_start()

    def on_connect(self, client, userdata, flags, rc):
        self.logger.info(f"Upstream MQTT Client connected. Is reconnect: {rc}")

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

        self.logger = logger.getChild(self.__class__.__name__)

    def auth(self):
        return RequestsSrpAuth(
            username=self.username,
            password=self.password,
            user_pool_id=constants.COGNITO_POOL_ID,
            client_id=constants.COGNITO_CLIENT_ID,
            user_pool_region=constants.COGNITO_POOL_REGION,
            auth_token_type=TokenType.ID_TOKEN
        )

    def get_account_data(self):
        self.logger.info(f"Retrieving account data for {self.username}")
        req = requests.Request('GET', constants.ENERA_ACCOUNT_URL,
                               headers={"X-Mandant": constants.ENERA_MANDANT, "Platform": "android"}, auth=self.auth())
        prepared = req.prepare()
        s = requests.Session()
        r = s.send(prepared)
        data = json.loads(r.text)
        self.devices = data["Devices"]
        self.live_uri = data["LiveURI"]

        return data

    def login(self):
        client_id = str(uuid.uuid4())
        client = boto3.client('cognito-idp', region_name=constants.COGNITO_POOL_REGION)
        aws = AWSSRP(
            username=self.username,
            password=self.password,
            pool_id=constants.COGNITO_POOL_ID,
            client_id=constants.COGNITO_CLIENT_ID,
            client=client
        )
        tokens = aws.authenticate_user()

        self.tokens = tokens['AuthenticationResult']
        self.tokens['ExpirationDateTime'] = datetime.now() + timedelta(seconds=self.tokens['ExpiresIn'])

        self.logger.debug(tokens)


def main():
    conn = BeeneraConnection(username=config.BEENERA_USER, password=config.BEENERA_PASSWORD)
    devices = conn.get_account_data()
    mqtt_conn = MqttConnection(conn, upstream_mqtt_config=config.MQTT)
    mqtt_conn.connect()

    try:
        mqtt_conn.mqtt_client.loop_forever()
    except KeyboardInterrupt:
        logger.info("Interrupted, exiting.")


if __name__ == '__main__':
    main()
