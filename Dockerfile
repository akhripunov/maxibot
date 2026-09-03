FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY docker/max-certs/rootca_ssl_rsa2022.crt docker/max-certs/subca_ssl_rsa2024.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
COPY pyproject.toml .
RUN pip install --no-cache-dir .[test]
COPY . .
RUN { cat /etc/ssl/certs/ca-certificates.crt; printf '\\n'; \
    cat /usr/local/share/ca-certificates/rootca_ssl_rsa2022.crt; printf '\\n'; \
    cat /usr/local/share/ca-certificates/subca_ssl_rsa2024.crt; } \
    > /etc/ssl/certs/maxibot-ca-bundle.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/maxibot-ca-bundle.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/maxibot-ca-bundle.crt
