import os
import re
import json
import subprocess
import threading
import time
import requests
import urllib3
from urllib.parse import urljoin
from flask import Flask, send_from_directory, jsonify

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HLS_DIR = "hls_stream"
os.makedirs(HLS_DIR, exist_ok=True)

app = Flask(__name__)

# Global Durum Değişkenleri ve Kilit (Lock)
ffmpeg_process = None
ffmpeg_lock = threading.Lock()
stream_start_time = 0
TOKEN_REFRESH_INTERVAL = 6800  # En Önleyici yenileme

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

    base_suffix = master_url.replace(".m3u8", "") if master_url else ""
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
    """Thread-safe FFmpeg başlatma fonksiyonu."""
    global ffmpeg_process, stream_start_time

    with ffmpeg_lock:
        url_1080p, url_720p, url_360p = get_dynamic_stream_urls()
        if not url_1080p or not url_720p or not url_360p:
            print("[HATA] Akış URL'leri alınamadı, FFmpeg başlatılamıyor.")
            return False

        create_master_manifest()

        # Var olan eski FFmpeg sürecini temizle
        if ffmpeg_process and ffmpeg_process.poll() is None:
            print("[BİLGİ] Eski FFmpeg süreci kapatılıyor...")
            ffmpeg_process.kill()
            ffmpeg_process.wait()

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
            os.path.join(HLS_DIR, "showturk_720p.m3u8"),
            # Çıktı 3: 360p
            "-map", "2:v?", "-map", "2:a?", "-c", "copy",
            "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
            "-hls_flags", "delete_segments+append_list",
            os.path.join(HLS_DIR, "showturk_360p.m3u8")
        ]

        ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stream_start_time = time.time()
        print("[BİLGİ] FFmpeg süreci başarıyla başlatıldı.")
        return True

# ==========================================
# 3. WATCHDOG (OTOMATİK İZLEYİCİ VE YENİLEYİCİ)
# ==========================================
def stream_watchdog():
    """Çökmeleri ve 2 saatlik önleyici yenilemeyi takip eder."""
    global ffmpeg_process, stream_start_time
    
    while True:
        time.sleep(8)
        now = time.time()
        
        # 1. Çökme Kontrolü (FFmpeg durmuşsa yeniden başlat)
        if ffmpeg_process is not None and ffmpeg_process.poll() is not None:
            print("[UYARI] FFmpeg beklenmedik şekilde durdu! Yeniden başlatılıyor...")
            start_ffmpeg_process()
            
        # 2. Önleyici 2 Saatlik Yenileme (Token/URL Süresi Dolmadan)
        elif ffmpeg_process is not None and (now - stream_start_time) >= TOKEN_REFRESH_INTERVAL:
            print("[BİLGİ] 2 Saatlik yayın süresi doldu. Önleyici token/URL yenilenmesi yapılıyor...")
            start_ffmpeg_process()

# Watchdog thread'ini başlat
threading.Thread(target=stream_watchdog, daemon=True).start()

# ==========================================
# 4. FLASK ROTASI VE ENDPOINT'LER
# ==========================================
@app.route("/")
def index():
    return """
    <h1>Show Türk HLS Streamer (Autonomous & Auto-Recover)</h1>
    <ul>
        <li><a href='/hls_stream/master.m3u8'>Master Playlist</a></li>
        <li><a href='/hls_stream/showturk_1080p.m3u8'>1080p Playlist</a></li>
        <li><a href='/hls_stream/showturk_720p.m3u8'>720p Playlist</a></li>
        <li><a href='/hls_stream/showturk_360p.m3u8'>360p Playlist</a></li>
        <li><a href='/health'>Health Status</a></li>
    </ul>
    """

@app.route("/hls_stream/<path:filename>", methods=["GET", "OPTIONS"])
def serve_hls(filename):
    global ffmpeg_process
    
    # 1. Tarayıcının OPTIONS (Preflight) isteğine onay ver
    if request.method == "OPTIONS":
        response = app.make_default_options_response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
        return response

    # 2. LAZY LOAD: İlk izleyici isteğinde yayın başlatılır
    if ffmpeg_process is None or ffmpeg_process.poll() is not None:
        print("[LAZY LOAD] İlk izleyici isteği geldi. CNN Türk yayını başlatılıyor...")
        start_ffmpeg_process()
        
    response = send_from_directory(HLS_DIR, filename)
    
    # 3. hls.js Uyumlu Tam CORS Başlıkları
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
    response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range"
    
    return response

@app.route("/health")
def health_check():
    """UptimeRobot ve sistem durumu denetimi için endpoint."""
    is_alive = ffmpeg_process is not None and ffmpeg_process.poll() is None
    next_refresh = max(0, int(TOKEN_REFRESH_INTERVAL - (time.time() - stream_start_time))) if is_alive else 0
    
    return jsonify({
        "status": "healthy" if is_alive else "idle/restarting",
        "ffmpeg_active": is_alive,
        "watchdog_active": True,
        "next_token_refresh_in_seconds": next_refresh
    }), 200

@app.route("/restart")
def manual_restart():
    """Acil durumlar için manuel restart endpoint'i."""
    success = start_ffmpeg_process()
    if success:
        return "Show Türk yayını başarıyla yeniden başlatıldı."
    return "Yayın başlatılamadı!", 500

if __name__ == "__main__":
    # İlk açılışta yayını otomatik başlat
    start_ffmpeg_process()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
