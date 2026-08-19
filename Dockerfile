# Python tabanlı hafif Alpine/Slim imajı
FROM python:3.10-slim

# Sistem güncellemeleri ve FFmpeg kurulumu
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Çalışma dizinini ayarla
WORKDIR /app

# Bağımlılıkları kopyala ve yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Proje dosyalarını kopyala
COPY . .

# HLS segmentlerinin yazılacağı dizini oluştur
RUN mkdir -p hls_stream

# Render / Konteyner portunu dışa aç
EXPOSE 10000

# Gunicorn ile uygulamayı başlat (Dosya adınız main.py ise 'main:app' yapın)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "4", "app:app"]
