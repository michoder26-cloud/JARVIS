# JARVIS cloud web app.
#   docker build -t jarvis .
#   docker run -p 8000:8000 -e GEMINI_API_KEY=... -e JARVIS_ACCESS_TOKEN=... jarvis
# Build with --build-arg WITH_WHISPER=1 to add server-side speech recognition
# (needs ~1 GB RAM; browsers with built-in speech recognition don't need it).
FROM python:3.12-slim

ARG WITH_WHISPER=0
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JARVIS_DATA_DIR=/data \
    JARVIS_TIMEZONE=Asia/Bangkok \
    PORT=8000

WORKDIR /app
COPY requirements-web.txt .
RUN pip install -r requirements-web.txt \
    && if [ "$WITH_WHISPER" = "1" ]; then pip install "faster-whisper>=1.2.0"; fi

COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
RUN pip install --no-deps . && mkdir -p /data

EXPOSE 8000
CMD ["python", "-m", "jarvis.web"]
