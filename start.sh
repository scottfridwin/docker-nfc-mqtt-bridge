#!/bin/sh
# Wait for the host's pcscd socket (mounted from /run/pcscd) before starting.
SOCKET="/run/pcscd/pcscd.comm"
while [ ! -S "$SOCKET" ]; do
    echo "[INFO] Waiting for pcscd socket at $SOCKET..."
    sleep 2
done

echo "[INFO] pcscd socket found. Starting NFC reader..."
exec python /app/nfc_reader.py
