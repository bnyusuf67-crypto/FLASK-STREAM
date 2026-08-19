# Hafif Python imajı
FROM python:3.9-slim

# Sadece FFmpeg kur (python3-pip zaten slim imajında yüklüdür)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Çalışma dizini
WORKDIR /app

# Bağımlılıkları yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodlarını kopyala
COPY . .

# Gunicorn'u TEK WORKER ve THREAD desteği ile başlat
CMD exec gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT:-10000} app:app
