import os
import re
import json
import subprocess
import threading
import time
import requests
import urllib3
from urllib.parse import urljoin
from flask import Flask, send_from_directory

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HLS_DIR = "hls_stream"
os.makedirs(HLS_DIR, exist_ok=True)

app = Flask(__name__)
ffmpeg_process = None
is_running = False

# ==========================================
# 1. SHOW TÜRK & DİNAMİK URL AYRIŞTIRMA
# ==========================================
def get_showturk_master_url():
    """Show Türk canlı yayın sayfasından ana M3U8 adresini çeker."""
    target_url = "https://www.showturk.com.tr/canli-yayin"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        response = requests.get(target_url, headers=headers, verify=False, timeout=15)
        if response.status_code == 200:
            match = re.search(r"data-hope-video='(.*?)'", response.text, re.DOTALL)
            if match:
                json_data_raw = match.group(1).replace("\\/", "/")
                ht_data = json.loads(json_data_raw)
                m3u8_list = ht_data.get("media", {}).get("m3u8", [])
                if m3u8_list:
                    return m3u8_list[0].get("src")
    except Exception as e:
        print(f"[HATA] Master URL çekilemedi: {e}")
    return None

def get_dynamic_stream_urls():
    """Master M3U8 dosyasını okuyup kalite bağlantılarını ayrıştırır."""
    master_url = get_showturk_master_url()
    if not master_url:
        return None, None, None

    try:
        res = requests.get(master_url, verify=False, timeout=10)
        if res.status_code == 200:
            lines = [line.strip() for line in res.text.splitlines() if line.strip() and not line.startswith("#")]
            full_urls = [urljoin(master_url, line) for line in lines]
            
            if len(full_urls) >= 3:
                return full_urls[0], full_urls[1], full_urls[2]
            elif len(full_urls) == 2:
                return full_urls[0], full_urls[1], full_urls[1]
            elif len(full_urls) == 1:
                return full_urls[0], full_urls[0], full_urls[0]
    except Exception as e:
        print(f"[HATA] Dinamik URL ayrıştırma başarısız: {e}")

    base_suffix = master_url.replace(".m3u8", "")
    return f"{base_suffix}.m3u8", f"{base_suffix}_720p.m3u8", f"{base_suffix}_360p.m3u8"

# ==========================================
# 2. MANİFEST VE FFMPEG AKIŞ YÖNETİMİ
# ==========================================
def create_master_manifest():
    """HLS istemcileri için master.m3u8 oluşturur."""
    master_content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1500000,RESOLUTION=1920x1080
showturk_1080p.m3u8
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1200000,RESOLUTION=1280x720
showturk_720p.m3u8
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=600000,RESOLUTION=640x360
showturk_360p.m3u8"""

    with open(os.path.join(HLS_DIR, "master.m3u8"), "w", encoding="utf-8") as f:
        f.write(master_content)

def start_ffmpeg_process():
    """FFmpeg sürecini başlatır."""
    global ffmpeg_process
    
    url_1080p, url_720p, url_360p = get_dynamic_stream_urls()
    if not url_1080p or not url_720p or not url_360p:
        print("[HATA] Akış URL'leri alınamadı, FFmpeg başlatılamıyor.")
        return False

    create_master_manifest()

    if ffmpeg_process and ffmpeg_process.poll() is None:
        ffmpeg_process.kill()

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        # 1. Giriş (1080p)
        "-reconnect", "1", "-reconnect_at_eof", "1", 
        "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", url_1080p,
        # 2. Giriş (720p)
        "-reconnect", "1", "-reconnect_at_eof", "1", 
        "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", url_720p,
        # 3. Giriş (360p)
        "-reconnect", "1", "-reconnect_at_eof", "1", 
        "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", url_360p,
        # Çıktı 1: 1080p
        "-map", "0:v?", "-map", "0:a?", "-c", "copy",
        "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list",
        os.path.join(HLS_DIR, "showturk_1080p.m3u8"),
        # Çıktı 2: 720p
        "-map", "1:v?", "-map", "1:a?", "-c", "copy",
        "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list",
        os.path.join(HLS_DIR, "showturk_720p.m3u8"),  # <-- Düzeltilen virgül burasıdır
        # Çıktı 3: 360p
        "-map", "2:v?", "-map", "2:a?", "-c", "copy",
        "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list",
        os.path.join(HLS_DIR, "showturk_360p.m3u8")
    ]

    ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("[BİLGİ] FFmpeg süreci başarıyla başlatıldı.")
    return True

# ==========================================
# 3. WATCHDOG (OTOMATİK İZLEYİCİ)
# ==========================================
def stream_watchdog():
    global ffmpeg_process, is_running
    while True:
        time.sleep(10)
        if is_running:
            if ffmpeg_process is None or ffmpeg_process.poll() is not None:
                print("[UYARI] FFmpeg durdu! Watchdog yayını yeniden başlatıyor...")
                start_ffmpeg_process()

# ==========================================
# FLASK ROTASI VE UYGULAMA BAŞLANGICI
# ==========================================
@app.route("/")
def index():
    return """
    <h1>Show Türk HLS Streamer (Auto-Recover)</h1>
    <ul>
        <li><a href='/start'>Başlatma Tuşu</a></li>
        <li><a href='/hls_stream/master.m3u8'>Master Playlist</a></li>
        <li><a href='/hls_stream/showturk_1080p.m3u8'>1080p Playlist</a></li>
        <li><a href='/hls_stream/showturk_720p.m3u8'>720p Playlist</a></li>
        <li><a href='/hls_stream/showturk_360p.m3u8'>360p Playlist</a></li>
    </ul>
    """

@app.route("/start")
def trigger_stream():
    global is_running
    is_running = True
    success = start_ffmpeg_process()
    if success:
        return "Yayın ve Watchdog başarıyla başlatıldı!"
    return "Yayın başlatılamadı!", 500

@app.route("/hls_stream/<path:filename>")
def serve_hls(filename):
    response = send_from_directory(HLS_DIR, filename)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

watchdog_thread = threading.Thread(target=stream_watchdog, daemon=True)
watchdog_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
