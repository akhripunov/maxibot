FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY app ./app
ARG RELEASE_SHA=UNKNOWN
ENV MAXIBOT_COMMIT_SHA=${RELEASE_SHA} PYTHONUNBUFFERED=1
LABEL org.maxibot.component=telegram-executor
LABEL org.maxibot.release.sha=${RELEASE_SHA}
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["uvicorn", "app.telegram_executor.main:app", "--host", "0.0.0.0", "--port", "8080"]
