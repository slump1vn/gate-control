FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gate_agent/ /app/gate_agent/

RUN useradd --system --uid 10001 --no-create-home agent
USER agent

# Prometheus metrics
EXPOSE 9101

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9101/metrics', timeout=3)"

ENTRYPOINT ["python", "-m", "gate_agent"]
CMD ["run"]
