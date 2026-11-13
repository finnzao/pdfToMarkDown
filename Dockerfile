FROM python:3.12-slim

# eng: idioma de reserva do OCR (por+eng); por já vem no repositório.
# O motor Tesseract está embutido no PyMuPDF, só os idiomas são externos.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr-eng tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    TZ=America/Bahia \
    PORTA=8077 \
    API_DIR_TEMP=/dados

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
RUN cp /usr/share/tesseract-ocr/*/tessdata/eng.traineddata app/pipeline/tessdata/

# Usuário sem privilégios; /dados guarda temporários, cache e lotes (24 h)
RUN useradd --create-home extrator && mkdir -p /dados && chown extrator /dados
USER extrator

EXPOSE 8077

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORTA'] + '/v1/health', timeout=4)"

# Um processo só: o estado dos lotes fica em memória e o OCR já usa todos
# os núcleos (um processo por página). --no-access-log: a URL de um item
# de lote contém o nome do arquivo (número do processo).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORTA} --workers 1 --no-access-log"]
