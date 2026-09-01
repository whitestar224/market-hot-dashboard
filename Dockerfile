ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XINGYUN_ENV=production \
    XINGYUN_HOST=0.0.0.0 \
    PORT=8765 \
    XINGYUN_LOAD_ENV_EXAMPLE=0 \
    XINGYUN_DISABLE_DESKTOP_ALERT=1 \
    XINGYUN_EXPOSE_DEV_EMAIL_CODE=0 \
    XINGYUN_EXPOSE_DEV_PHONE_CODE=0

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin xingyun

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/.runtime-cache && chown -R xingyun:xingyun /app

USER xingyun

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).read()"

CMD ["sh", "-c", "python server.py --host ${XINGYUN_HOST:-0.0.0.0} --port ${PORT:-8765}"]
