FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY docker/max-certs/*.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
COPY pyproject.toml .
RUN pip install --no-cache-dir .[test]
COPY . .
