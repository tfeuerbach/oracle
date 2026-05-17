FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY oracle/ oracle/

RUN mkdir -p /app/data /app/tmp

VOLUME ["/app/data"]

CMD ["python", "-m", "oracle"]
