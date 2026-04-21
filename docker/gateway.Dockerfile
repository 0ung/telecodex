FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/
COPY src /app/src
COPY config /app/config

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "telecodex.gateway.main"]
CMD ["--config", "config/gateway.example.yaml"]
