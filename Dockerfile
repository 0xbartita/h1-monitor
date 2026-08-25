FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY h1monitor ./h1monitor
RUN pip install --no-cache-dir .
# State (SQLite DB + Fernet key) is written to the working directory; run from
# /data so a single mounted volume persists everything across restarts.
WORKDIR /data
VOLUME /data
CMD ["python", "-m", "h1monitor"]
