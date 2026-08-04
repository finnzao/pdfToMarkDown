FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# Usuário sem privilégios
RUN useradd --create-home extrator
USER extrator

EXPOSE 8077

# 2 workers: processamento é CPU-bound; ajuste ao nº de vCPUs do host
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8077", "--workers", "2"]
