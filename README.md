# NFC → MQTT Bridge

[![Build](https://img.shields.io/github/actions/workflow/status/scottfridwin/docker-nfc-mqtt-bridge/build.yml?branch=main&label=build)](https://github.com/scottfridwin/docker-nfc-mqtt-bridge/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/scottfridwin/docker-nfc-mqtt-bridge)](https://github.com/scottfridwin/docker-nfc-mqtt-bridge/releases/latest)
[![Image](https://img.shields.io/badge/image-ghcr.io-blue?logo=docker)](https://github.com/scottfridwin/docker-nfc-mqtt-bridge/pkgs/container/nfc-mqtt-bridge)
[![License](https://img.shields.io/github/license/scottfridwin/docker-nfc-mqtt-bridge)](LICENSE)

Turn a USB NFC reader into a [Home Assistant](https://www.home-assistant.io/) tag scanner. The container reads tag UIDs through the host's PC/SC daemon (`pcscd`) and publishes them to MQTT, with Home Assistant discovery so the reader shows up on its own.

> [!NOTE]
> **AI disclosure:** This project is built and maintained with substantial help from AI coding assistants (GitHub Copilot). AI is used to write and modify the code, tests, documentation and CI configuration, and to manage the repository. Dependency updates are merged and released automatically, without human review, when the automated tests pass. Review the code and test it in your own environment before relying on it.

## Features

- **Home Assistant tag scanner** — scans appear under **Settings → Tags** and can trigger automations
- **Tag UID sensor** — shows the tag currently on the reader, cleared when it is removed
- **Availability** — the reader shows as unavailable when the container stops or loses its connection
- **Works with any PC/SC reader** supported by `pcscd` (for example ACR122U)
- **Multi-architecture image** for `amd64`, `arm64` and `arm/v7` (Raspberry Pi)
- **Secure by default** — runs as non-root, works with a read-only filesystem and no Linux capabilities, supports MQTT over TLS and Docker secrets
- **Health check** — Docker marks the container unhealthy if the bridge stops working
- **Verifiable images** — signed build provenance and an SBOM for every image

## Requirements

- A Linux host with a USB NFC reader and `pcscd` running:

  ```bash
  sudo apt install pcscd pcsc-tools
  sudo systemctl enable --now pcscd
  pcsc_scan   # place a tag on the reader to check it works
  ```

- An MQTT broker that Home Assistant uses (for example the Mosquitto add-on)

## Quick start

```yaml
services:
  nfc-mqtt-bridge:
    image: ghcr.io/scottfridwin/nfc-mqtt-bridge:1
    container_name: nfc-mqtt-bridge
    restart: unless-stopped
    environment:
      MQTT_HOST: homeassistant.local
      MQTT_USERNAME: nfc
      MQTT_PASSWORD_FILE: /run/secrets/mqtt_password
      DEVICE_ID: nfc_reader_hall
      DEVICE_NAME: Hall NFC Reader
    secrets:
      - mqtt_password
    volumes:
      - /run/pcscd:/run/pcscd   # the host's pcscd socket
    # Optional hardening; the bridge needs no capabilities and only writes to /tmp
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true

secrets:
  mqtt_password:
    file: ./mqtt_password.txt
```

```bash
printf '%s' 'your-mqtt-password' > mqtt_password.txt && chmod 600 mqtt_password.txt
docker compose up -d
```

The container waits for the `pcscd` socket, connects to MQTT and registers the reader with Home Assistant.

## Configuration

| Variable | Description | Default |
| --- | --- | --- |
| `MQTT_HOST` | MQTT broker host | `localhost` |
| `MQTT_PORT` | MQTT broker port | `1883`, or `8883` with TLS |
| `MQTT_TLS` | Connect with TLS (`true`/`false`) | `false` |
| `MQTT_TLS_CA_FILE` | CA certificate for the broker, if it is not signed by a public CA | system CAs |
| `MQTT_TLS_INSECURE` | Skip the certificate host name check (only for brokers reached by IP address) | `false` |
| `MQTT_USERNAME` | MQTT username (omit for anonymous) | — |
| `MQTT_PASSWORD` | MQTT password | — |
| `MQTT_PASSWORD_FILE` | File containing the password, e.g. a Docker secret; ignored if `MQTT_PASSWORD` is set | — |
| `DEVICE_ID` | Unique ID for this reader; used in topics and Home Assistant IDs (letters, digits, `_`, `-`) | `nfc_reader` |
| `DEVICE_NAME` | Display name in Home Assistant | `NFC Reader <DEVICE_ID>` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` or `ERROR` | `INFO` |

Use a different `DEVICE_ID` for each reader.

### MQTT over TLS

Without TLS, the MQTT password and tag IDs cross the network unencrypted. If your broker has a TLS listener (the Mosquitto add-on can use your Home Assistant certificate), set `MQTT_TLS=true` and, for a private CA, mount its certificate and point `MQTT_TLS_CA_FILE` at it. The bridge refuses to connect if the broker's certificate cannot be verified.

## Home Assistant

The reader appears as a device with a **Tag UID** sensor and a tag scanner. Each scanned tag is added under **Settings → Tags**, where you can name it and create automations, or trigger on it directly:

```yaml
automation:
  - alias: Play music when the speaker tag is scanned
    triggers:
      - trigger: tag
        tag_id: 04A1B2C3D4E5F6
    actions:
      - action: media_player.media_play
        target:
          entity_id: media_player.living_room
```

### MQTT topics

For other MQTT consumers (`<id>` is `DEVICE_ID`):

| Topic | Payload |
| --- | --- |
| `homeassistant/event/<id>/tag_scanned` | `{"tag_uid": "04A1B2C3D4E5F6"}` when a tag is placed on the reader |
| `homeassistant/sensor/<id>/uid/state` | The UID while a tag is present, empty when it is removed |
| `homeassistant/sensor/<id>/availability` | `online` / `offline` (retained) |

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Log repeats *Waiting for pcscd socket* | `pcscd` is not running on the host, or `/run/pcscd` is not mounted into the container. |
| Connected, but tags are never detected | Run `pcsc_scan` on the host. If it sees the tag, check the container log with `LOG_LEVEL=DEBUG`. |
| PC/SC access denied | Recent `pcscd` packages use polkit and may refuse users without a login session. Allow the container's UID for `org.debian.pcsc-lite.access_pcsc` and `org.debian.pcsc-lite.access_card` in a polkit rule. |
| Reader shows as unavailable in Home Assistant | Check the MQTT host and credentials in the container log. |
| Container is *unhealthy* | The bridge has not completed a reader poll for 30 seconds: usually it is still waiting for `pcscd`, or it has stopped. Check the log. With `read_only: true`, `/tmp` must be writable (a `tmpfs`). |
| *CERTIFICATE_VERIFY_FAILED* | The broker's certificate is not trusted: set `MQTT_TLS_CA_FILE`, and make sure `MQTT_HOST` matches a name in the certificate. |

## Security

See [SECURITY.md](SECURITY.md) for how to report a vulnerability and how to verify an image with `gh attestation verify`.

## Further reading

- [Development](docs/development.md)

## License

[GPL-3.0](LICENSE)
