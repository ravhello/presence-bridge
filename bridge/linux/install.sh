#!/bin/sh
set -eu
if [ "$(id -u)" != 0 ]; then echo 'Run with sudo; BlueZ identity files require host administrator access.' >&2; exit 1; fi
command -v python3 >/dev/null
command -v systemctl >/dev/null
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TARGET=/opt/presence-bridge
if [ -e "$TARGET" ]; then echo 'Existing installation preserved. Follow docs/linux.md upgrade instructions.' >&2; exit 1; fi
umask 077
mkdir -p "$TARGET/bridge/linux" "$TARGET/custom_components/presence_bridge/receiver" /etc/presence-bridge
cp "$SOURCE/bridge/linux/receiver.py" "$SOURCE/bridge/linux/requirements.txt" "$TARGET/bridge/linux/"
cp "$SOURCE/custom_components/presence_bridge/protocol.py" "$TARGET/custom_components/presence_bridge/"
cp "$SOURCE"/custom_components/presence_bridge/receiver/*.py "$TARGET/custom_components/presence_bridge/receiver/"
python3 -m venv "$TARGET/.venv"
"$TARGET/.venv/bin/python" -m pip install -r "$TARGET/bridge/linux/requirements.txt"
if [ ! -e /etc/presence-bridge/config.json ]; then
    cp "$SOURCE/bridge/linux/config.example.json" /etc/presence-bridge/config.json
fi
chmod 600 /etc/presence-bridge/config.json
cp "$SOURCE/bridge/linux/presence-bridge.service" /etc/systemd/system/presence-bridge.service
systemctl daemon-reload
echo 'Installed but NOT started. Edit /etc/presence-bridge/config.json, run --doctor, then enable the service. See docs/linux.md.'
