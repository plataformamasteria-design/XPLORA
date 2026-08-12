FROM python:3.11-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99 \
    HOST=0.0.0.0 \
    PORT=8787 \
    MEET_STORAGE_DIR=/app/storage \
    MEET_RECORDINGS_DIR=/app/storage/recordings \
    MEET_MAX_MINUTES=0 \
    WHISPER_MODEL=small \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE_TYPE=int8

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg pulseaudio pulseaudio-utils xvfb gosu \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 meetbot \
    && mkdir -p /app/storage/recordings /app/storage/perfil-meet /app/storage/data /run/user/10001 \
    && chown -R meetbot:meetbot /app /run/user/10001

COPY --chown=meetbot:meetbot . .
RUN chmod +x /app/start.sh

EXPOSE 8787
CMD ["/app/start.sh"]
