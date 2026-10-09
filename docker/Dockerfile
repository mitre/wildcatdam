FROM python:3.12-slim

WORKDIR /app

# python3-tk: dam_system.py imports tkinter at module load even with --no-gui
RUN apt-get update && apt-get install -y --no-install-recommends python3-tk \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY dam_system.py .
COPY hmi.py .
COPY config.yaml .

# Modbus/TCP and web HMI
EXPOSE 5020 8090

# Modbus server; the web HMI runs as a separate service (hmi.py) over the wire.
ENTRYPOINT ["python", "dam_system.py", "--no-gui"]
