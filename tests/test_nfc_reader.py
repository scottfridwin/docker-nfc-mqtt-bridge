import json
import os
import tempfile
import unittest

from smartcard.Exceptions import NoCardException

import nfc_reader
from nfc_reader import Config, TagMonitor, discovery_messages, format_uid, get_env_or_file


class FakeConnection:
    def __init__(self, card):
        self._card = card
        self.connected = False

    def connect(self):
        if self._card is None:
            raise NoCardException("no card", 0)
        self.connected = True

    def transmit(self, apdu):
        assert apdu == nfc_reader.GET_UID_APDU
        return self._card

    def disconnect(self):
        self.connected = False


class FakeReader:
    """card is None (no tag) or a (data, sw1, sw2) response."""

    def __init__(self):
        self.card = None
        self.connections = []

    def createConnection(self):  # noqa: N802 - pyscard API name
        connection = FakeConnection(self.card)
        self.connections.append(connection)
        return connection


def config(**overrides):
    values = {
        "mqtt_host": "broker",
        "mqtt_port": 1883,
        "mqtt_username": None,
        "mqtt_password": None,
        "device_id": "reader1",
        "device_name": "Reader 1",
    }
    values.update(overrides)
    return Config(**values)


class GetEnvOrFileTests(unittest.TestCase):
    def test_value(self):
        self.assertEqual(get_env_or_file("PW", {"PW": "secret"}), "secret")

    def test_file_is_read_and_stripped(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("from-file\n")
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(get_env_or_file("PW", {"PW_FILE": handle.name}), "from-file")

    def test_value_wins_over_file(self):
        self.assertEqual(get_env_or_file("PW", {"PW": "direct", "PW_FILE": "/nope"}), "direct")

    def test_missing_file_returns_none(self):
        self.assertIsNone(get_env_or_file("PW", {"PW_FILE": "/nonexistent/secret"}))

    def test_unset_returns_none(self):
        self.assertIsNone(get_env_or_file("PW", {}))


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = Config.from_env({})
        self.assertEqual((cfg.mqtt_host, cfg.mqtt_port), ("localhost", 1883))
        self.assertEqual(cfg.device_id, "nfc_reader")
        self.assertEqual(cfg.device_name, "NFC Reader nfc_reader")
        self.assertIsNone(cfg.mqtt_username)

    def test_topics_follow_device_id(self):
        cfg = Config.from_env({"DEVICE_ID": "hall", "MQTT_PORT": "8883"})
        self.assertEqual(cfg.mqtt_port, 8883)
        self.assertEqual(cfg.state_topic, "homeassistant/sensor/hall/uid/state")
        self.assertEqual(cfg.availability_topic, "homeassistant/sensor/hall/availability")
        self.assertEqual(cfg.tag_topic, "homeassistant/event/hall/tag_scanned")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.messages = dict(discovery_messages(config()))

    def test_sensor_is_valid_for_home_assistant(self):
        sensor = self.messages["homeassistant/sensor/reader1/uid/config"]
        self.assertNotIn("device_class", sensor)
        self.assertEqual(sensor["state_topic"], "homeassistant/sensor/reader1/uid/state")
        self.assertEqual(sensor["availability_topic"], "homeassistant/sensor/reader1/availability")
        self.assertEqual(sensor["unique_id"], "reader1_uid")

    def test_tag_scanner(self):
        tag = self.messages["homeassistant/tag/reader1/config"]
        self.assertEqual(tag["topic"], "homeassistant/event/reader1/tag_scanned")
        self.assertEqual(tag["value_template"], "{{ value_json.tag_uid }}")
        self.assertEqual(tag["device"]["identifiers"], ["reader1"])

    def test_payloads_are_json_serializable(self):
        for payload in self.messages.values():
            json.dumps(payload)


class FormatUidTests(unittest.TestCase):
    def test_uppercase_hex(self):
        self.assertEqual(format_uid([0x04, 0xA1, 0x0F, 0xFF]), "04A10FFF")


class TagMonitorTests(unittest.TestCase):
    def setUp(self):
        self.published = []
        self.reader = FakeReader()
        self.monitor = TagMonitor(
            config(), lambda topic, payload: self.published.append((topic, payload)), lambda: [self.reader]
        )

    def test_tag_published_once_while_present(self):
        self.reader.card = ([0x04, 0xA1], 0x90, 0x00)
        self.monitor.poll()
        self.monitor.poll()
        self.assertEqual(
            self.published,
            [
                ("homeassistant/sensor/reader1/uid/state", "04A1"),
                ("homeassistant/event/reader1/tag_scanned", json.dumps({"tag_uid": "04A1"})),
            ],
        )

    def test_removal_clears_state(self):
        self.reader.card = ([0x04, 0xA1], 0x90, 0x00)
        self.monitor.poll()
        self.reader.card = None
        self.monitor.poll()
        self.monitor.poll()
        self.assertEqual(self.published[-1], ("homeassistant/sensor/reader1/uid/state", ""))
        self.assertEqual(len(self.published), 3)
        self.assertIsNone(self.monitor.last_uid)

    def test_new_tag_after_removal_is_published(self):
        self.reader.card = ([0x01], 0x90, 0x00)
        self.monitor.poll()
        self.reader.card = ([0x02], 0x90, 0x00)
        self.monitor.poll()
        uids = [payload for topic, payload in self.published if topic.endswith("/uid/state")]
        self.assertEqual(uids, ["01", "02"])

    def test_failed_read_publishes_nothing(self):
        self.reader.card = ([], 0x6A, 0x81)
        with self.assertLogs("nfc", level="WARNING"):
            self.monitor.poll()
        self.assertEqual(self.published, [])

    def test_connections_are_closed(self):
        self.reader.card = ([0x04], 0x90, 0x00)
        self.monitor.poll()
        self.assertTrue(all(not connection.connected for connection in self.reader.connections))

    def test_no_readers_is_quiet(self):
        monitor = TagMonitor(config(), lambda *_: self.fail("should not publish"), lambda: [])
        monitor.poll()


class MqttClientTests(unittest.TestCase):
    def test_last_will_and_republish_on_connect(self):
        client = nfc_reader.create_client(config(mqtt_username="user", mqtt_password="pw"))
        self.assertEqual(client._will_topic.decode(), "homeassistant/sensor/reader1/availability")
        self.assertEqual(client._will_payload, b"offline")
        self.assertTrue(client._will_retain)

        published = []
        client.publish = lambda topic, payload, retain=False: published.append((topic, retain))
        success = nfc_reader.mqtt.ReasonCode(nfc_reader.mqtt.PacketTypes.CONNACK, identifier=0)
        client.on_connect(client, None, None, success, None)

        self.assertEqual(
            published,
            [
                ("homeassistant/sensor/reader1/uid/config", True),
                ("homeassistant/tag/reader1/config", True),
                ("homeassistant/sensor/reader1/availability", True),
            ],
        )


if __name__ == "__main__":
    unittest.main()
