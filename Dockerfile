FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY feedgen ./feedgen

# config.toml, docs/ и cache/ монтируются снаружи (см. docker-compose.yml)
VOLUME ["/app/docs", "/app/cache"]
EXPOSE 8080

HEALTHCHECK --interval=5m --timeout=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1

CMD ["python", "-m", "feedgen", "--config", "/app/config.toml", "serve", "--port", "8080"]
