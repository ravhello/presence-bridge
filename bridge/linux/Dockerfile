FROM python:3.13-slim-bookworm
WORKDIR /opt/presence-bridge
COPY bridge/linux/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY custom_components/presence_bridge/protocol.py custom_components/presence_bridge/protocol.py
COPY custom_components/presence_bridge/receiver custom_components/presence_bridge/receiver
COPY bridge/linux/receiver.py bridge/linux/receiver.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "bridge/linux/receiver.py", "--config", "/etc/presence-bridge/config.json"]
