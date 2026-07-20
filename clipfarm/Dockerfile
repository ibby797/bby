FROM python:3.12-slim

# ffmpeg does the video work; DejaVu supplies the caption font
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY clipfarm ./clipfarm
COPY web ./web
COPY run.py .

ENV CLIPFARM_DATA=/data
VOLUME /data
EXPOSE 8000

# Most PaaS hosts inject PORT; default to 8000 for plain Docker
CMD ["sh", "-c", "uvicorn clipfarm.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
