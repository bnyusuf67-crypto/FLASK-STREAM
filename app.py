import os
import re
import json
import subprocess
import threading
import time
import requests
import urllib3
from flask import Flask, send_from_directory

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HLS_DIR = "hls_stream"
os.makedirs(HLS_DIR, exist_ok=True)

app = Flask(__name__)
ffmpeg_process = None

def get_showturk_hls_url():
    """Show Türk canlı yayın sayfasından ana M3U8 URL'sini çeker."""
    target_url = "https://www.showturk.com.tr/canli-yayin"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        response = requests.get(target_url, headers=headers, verify=False, timeout=15)
        if response.status_code != 200:
            return None

        match = re.search(r"data-hope-video='(.*?)'", response.text, re.DOTALL)
        if not match:
            return None

        json_data_raw = match.group(1).replace("\\/", "/")
        ht_data = json.loads(json_data_raw)

        m3u8_list = ht_data.get("media", {}).get("m3u8", [])
        if m3u8_list:
            return m3u8_list[0].get("src")

    except Exception as e:
        print(f"Show Türk URL alma hatası: {e}")

    return None

def create_master_manifest():
    """Master.m3u8 dosyasını yerel çıktılara yönlendirir."""
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

def build_variant_url(base_url, suffix):
    if ".m3u8" in base_url:
        return base_url.replace(".m3u8", f"{suffix}.m3u8")
    return base_url

def start_stream_generator():
    global ffmpeg_process

    base_m3u8_url = get_showturk_hls_url()
    if not base_m3u8_url:
        print("M3U8 adresi alınamadı!")
        return

    # Show Türk akış yapısına uygun varyasyon bağlantıları
    url_1080p = build_variant_url(base_m3u8_url, "_1080p")
    url_720p = build_variant_url(base_m3u8_url, "_720p")
    url_360p = build_variant_url(base_m3u8_url, "_360p")

    create_master_manifest()

    if ffmpeg_process and ffmpeg_process.poll() is None:
        ffmpeg_process.kill()

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-re", "-i", url_1080p,
        "-re", "-i", url_720p,
        "-re", "-i", url_360p,

        # 1080p Akışı -> showturk_1080p.m3u8
        "-map", "0:v?", "-map", "0:a?", "-c", "copy",
        "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list",
        os.path.join(HLS_DIR, "showturk_1080p.m3u8"),

        # 720p Akışı -> showturk_720p.m3u8
        "-map", "1:v?", "-map", "1:a?", "-c", "copy",
        "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list",
        os.path.join(HLS_DIR, "showturk_720p.m3u8"),

        # 360p Akışı -> showturk_360p.m3u8
        "-map", "2:v?", "-map", "2:a?", "-c", "copy",
        "-f", "hls", "-hls_time", "4", "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list",
        os.path.join(HLS_DIR, "showturk_360p.m3u8")
    ]

    ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def periodic_site_trigger():
    # Render veya hosting platformunda uygulamanın uyumaması için kendisini çağıran döngü
    port = os.environ.get("PORT", "10000")
    target_start_url = f"http://127.0.0.1:{port}/start"
    time.sleep(5)
    while True:
        try:
            requests.get(target_start_url, timeout=10)
        except Exception as e:
            print(f"Tetikleme hatası: {e}")
        time.sleep(6800)

@app.route("/")
def index():
    return """
    <h1>Show Türk HLS Streamer</h1>
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
    try:
        start_stream_generator()
        return "Yayın başarıyla başlatıldı!"
    except Exception as e:
        return f"Hata: {str(e)}", 500

@app.route("/hls_stream/<path:filename>")
def serve_hls(filename):
    response = send_from_directory(HLS_DIR, filename)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

# Sunucu veya script başladığında thread derhal devreye girer
def init_background_thread():
    trigger_thread = threading.Thread(target=periodic_site_trigger, daemon=True)
    trigger_thread.start()

init_background_thread()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
