FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# pdfplumber needs libpoppler for some PDFs; pypdfium2 ships its own native lib.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

# --server.address 0.0.0.0 → reachable from other containers
# --server.headless true   → no "open browser" prompt on boot
# --server.enableCORS false + --server.enableXsrfProtection false →
#     Caddy is the public boundary, and Streamlit's own CORS/XSRF stack
#     interferes with reverse-proxied websockets in some configs.
CMD ["streamlit", "run", "Tableau_de_bord.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false"]
