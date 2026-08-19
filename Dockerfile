FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY h1monitor ./h1monitor
RUN pip install --no-cache-dir .
# State (SQLite DB + Fernet keyfile) is written to the working directory.
CMD ["python", "-m", "h1monitor"]
