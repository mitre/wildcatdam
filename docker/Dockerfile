FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY simulator/ simulator/
COPY config.yaml .
EXPOSE 5020 8090
ENTRYPOINT ["python", "-m", "simulator.plc"]
