FROM python:3.12-slim
RUN groupadd -r appuser && useradd -r -g appuser -u 10001 appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"
CMD ["uvicorn", "schwab_data_proxy.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
