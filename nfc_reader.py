#!/usr/bin/env python3
"""Read NFC tag UIDs from a PC/SC reader and publish them to MQTT for Home Assistant."""

import json
import logging
import os
import signal
import threading
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from smartcard.Exceptions import CardConnectionException, NoCardException
from smartcard.pcsc.PCSCExceptions import EstablishContextException
from smartcard.System import readers as list_readers

log = logging.getLogger("nfc")

# PC/SC "Get Data" command that returns the UID on most contactless readers
GET_UID_APDU = [0xFF, 0xCA, 0x00, 0x00, 0x00]
POLL_INTERVAL = 0.5
RETRY_INTERVAL = 2.0


def get_env_or_file(name, environ=None):
    """Return NAME, or the contents of the file named by NAME_FILE (NAME wins)."""
    environ = os.environ if environ is None else environ
    value = environ.get(name)
    file_path = environ.get(f"{name}_FILE")

    if value and file_path:
        log.warning("Both %s and %s_FILE are set; using %s", name, name, name)
    if value:
        return value
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as secret_file:
                return secret_file.read().strip()
        except OSError as exc:
            log.error("Unable to read %s_FILE '%s': %s", name, file_path, exc)
    return None


@dataclass(frozen=True)
class Config:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    device_id: str
    device_name: str

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        device_id = environ.get("DEVICE_ID", "nfc_reader")
        return cls(
            mqtt_host=environ.get("MQTT_HOST", "localhost"),
            mqtt_port=int(environ.get("MQTT_PORT", "1883")),
            mqtt_username=environ.get("MQTT_USERNAME") or None,
            mqtt_password=get_env_or_file("MQTT_PASSWORD", environ),
            device_id=device_id,
            device_name=environ.get("DEVICE_NAME", f"NFC Reader {device_id}"),
        )

    @property
    def state_topic(self):
        return f"homeassistant/sensor/{self.device_id}/uid/state"

    @property
    def availability_topic(self):
        return f"homeassistant/sensor/{self.device_id}/availability"

    @property
    def tag_topic(self):
        return f"homeassistant/event/{self.device_id}/tag_scanned"


def discovery_messages(config):
    """Home Assistant MQTT discovery configs: a UID sensor and a tag scanner."""
    device = {
        "identifiers": [config.device_id],
        "name": config.device_name,
        "manufacturer": "DIY",
        "model": "PC/SC NFC reader",
    }
    sensor = {
        "name": "Tag UID",
        "unique_id": f"{config.device_id}_uid",
        "state_topic": config.state_topic,
        "availability_topic": config.availability_topic,
        "icon": "mdi:nfc",
        "device": device,
    }
    tag_scanner = {
        "topic": config.tag_topic,
        "value_template": "{{ value_json.tag_uid }}",
        "device": device,
    }
    return [
        (f"homeassistant/sensor/{config.device_id}/uid/config", sensor),
        (f"homeassistant/tag/{config.device_id}/config", tag_scanner),
    ]


def format_uid(data):
    return "".join(f"{byte:02X}" for byte in data)


class TagMonitor:
    """Publishes a tag's UID when it is placed on a reader and clears it when removed."""

    def __init__(self, config, publish, readers=list_readers):
        self._config = config
        self._publish = publish
        self._readers = readers
        self.last_uid = None

    def poll(self):
        for reader in self._readers():
            connection = reader.createConnection()
            try:
                connection.connect()
            except NoCardException:
                self._tag_removed()
                continue
            try:
                data, sw1, sw2 = connection.transmit(GET_UID_APDU)
            finally:
                connection.disconnect()

            if sw1 != 0x90:
                log.warning("Failed to read UID from tag: SW1=%02X, SW2=%02X", sw1, sw2)
                continue
            self._tag_present(format_uid(data))

    def _tag_present(self, uid):
        if uid == self.last_uid:
            return
        log.info("Tag detected: %s", uid)
        self._publish(self._config.state_topic, uid)
        self._publish(self._config.tag_topic, json.dumps({"tag_uid": uid}))
        self.last_uid = uid

    def _tag_removed(self):
        if self.last_uid is None:
            return
        log.info("Tag removed")
        self._publish(self._config.state_topic, "")
        self.last_uid = None


def create_client(config):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.will_set(config.availability_topic, "offline", retain=True)
    if config.mqtt_username:
        client.username_pw_set(config.mqtt_username, config.mqtt_password)

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            log.error("MQTT connection refused: %s", reason_code)
            return
        log.info("Connected to MQTT broker at %s:%s", config.mqtt_host, config.mqtt_port)
        # Republish on every (re)connect so Home Assistant recovers after a broker restart
        for topic, payload in discovery_messages(config):
            client.publish(topic, json.dumps(payload), retain=True)
        client.publish(config.availability_topic, "online", retain=True)

    def on_disconnect(_client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            log.warning("Disconnected from MQTT broker (%s); reconnecting", reason_code)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    return client


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = Config.from_env()
    log.info("Starting NFC MQTT Bridge for %s", config.device_id)

    client = create_client(config)
    client.connect(config.mqtt_host, config.mqtt_port, keepalive=60)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    monitor = TagMonitor(config, lambda topic, payload: client.publish(topic, payload, qos=1))
    try:
        while not stop.is_set():
            try:
                monitor.poll()
                delay = POLL_INTERVAL
            except (EstablishContextException, CardConnectionException) as exc:
                log.warning("PC/SC error: %s", exc)
                monitor.last_uid = None
                delay = RETRY_INTERVAL
            except Exception:  # pylint: disable=broad-except
                log.exception("Unexpected error in monitor loop")
                delay = RETRY_INTERVAL
            stop.wait(delay)
    finally:
        log.info("Shutting down NFC reader...")
        client.publish(config.availability_topic, "offline", retain=True).wait_for_publish(timeout=5)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
