import subprocess
import os
import glob
import logging
import time
from datetime import datetime

# --- KONFIGURATION ---
MOUNT_PATH = "/home/osmc/recordings/growbox/timelapse"
IMAGE_DIR = os.path.join(MOUNT_PATH, "pics")
VIDEO_DIR = os.path.join(MOUNT_PATH, "videos")
COUNTER_FILE = os.path.join(MOUNT_PATH, "counter.txt")
LOG_FILE = "/home/osmc/timelapse_error.log" # Log liegt auf der SD-Karte (sicher)

# BILD-EINSTELLUNGEN (FFmpeg eq filter)
# Werte: 1.0 ist Standard. 
BRIGHTNESS = 0.05 # -1.0 bis 1.0 (0.0 ist Standard)
CONTRAST = 1.1    # -2.0 bis 2.0 (1.0 ist Standard)
SATURATION = 1.2  # 0.0 bis 3.0 (1.0 ist Standard)
GAMMA = 1.0       # 0.1 bis 10.0 (1.0 ist Standard)

# Zu stoppende Dienste (OSMC Kodi = mediacenter)
SERVICES_TO_STOP = ["mediacenter", "snapclient"]

# ZEITSTEUERUNG (18:00 Uhr bis 06:00 Uhr morgens)
START_HOUR = 18
END_HOUR = 6
WARMUP_SEC = 10  # Wartezeit in Sekunden nach Licht-Einschalten

MAX_IMAGES = 1008             
WINDOW_SIZE = 1008            

# Kamera & Video Settings
DEVICE = "/dev/video0"
RESOLUTION = "1920x1080"
FPS_OUT = 30
BITRATE = "25M"
# ---------------------

# Logging konfigurieren
logging.basicConfig(filename=LOG_FILE, level=logging.ERROR, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

def wait_for_light():
    # Wartet kurz, falls wir uns in der ersten Minute der Startzeit befinden.
    now = datetime.now()
    # Wenn es exakt die Startstunde und die erste Minute (00) ist
    if now.hour == START_HOUR and now.minute == 0:
        logging.info(f"Startzeit erreicht. Warte {WARMUP_SEC}s auf das Licht...")
        time.sleep(WARMUP_SEC)

def check_and_stop_services():
    # Prüft Dienste und stoppt sie, falls sie aktiv sind
    for service in SERVICES_TO_STOP:
        try:
            # Prüfen, ob der Dienst aktiv ist
            check = subprocess.run(['systemctl', 'is-active', '--quiet', service])
            if check.returncode == 0:
                logging.info(f"Dienst {service} war aktiv. Stoppe Dienst...")
                subprocess.run(['sudo', 'systemctl', 'stop', service], check=True)
        except Exception as e:
            logging.error(f"Fehler beim Stoppen von {service}: {e}")

def is_active_time():
    current_hour = datetime.now().hour
    # Logik für Zeitspannen über Mitternacht hinweg
    if START_HOUR > END_HOUR:
        return current_hour >= START_HOUR or current_hour < END_HOUR
    else:
        return START_HOUR <= current_hour < END_HOUR

def is_mounted(path):
    # Löst den Symlink auf den tatsächlichen Pfad (/mnt/ssd) auf
    real_path = os.path.realpath(path)
    
    # Prüft nun, ob dieser reale Pfad ein Mountpoint ist
    # ODER ob der reale Pfad zumindest im Mount-Verzeichnis liegt
    return os.path.ismount(real_path) or "/mnt/ssd" in real_path

def get_next_counter():
    if not os.path.exists(COUNTER_FILE):
        return 1
    with open(COUNTER_FILE, "r") as f:
        try:
            return int(f.read().strip()) + 1
        except Exception as e:
            logging.error(f"Fehler beim Lesen des Counters: {e}")
            return 1

def save_counter(count):
    try:
        with open(COUNTER_FILE, "w") as f:
            f.write(str(count))
    except Exception as e:
        logging.error(f"Fehler beim Speichern des Counters: {e}")

def capture_image(count):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(IMAGE_DIR, f"{count:06d}_{timestamp}.jpg")
    
    # Der eq-Filter (equalizer) für Helligkeit, Kontrast, Sättigung
    # Format: eq=brightness=0.05:contrast=1.1:saturation=1.2:gamma=1.0
    vf_params = f"eq=brightness={BRIGHTNESS}:contrast={CONTRAST}:saturation={SATURATION}:gamma={GAMMA}"
    
    cmd = [
        'ffmpeg', '-y', '-f', 'v4l2', '-video_size', RESOLUTION,
        '-i', DEVICE, 
        '-vf', vf_params,       # Filter anwenden
        '-frames:v', '1', 
        '-q:v', '2', 
        filename
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    
    if result.returncode != 0:
        logging.error(f"Kamera-Fehler (Bild {count}): {result.stderr}")
        return False
    return True

def create_video():
    try:
        all_files = sorted(glob.glob(os.path.join(IMAGE_DIR, "*.jpg")))
        last_files = all_files[-WINDOW_SIZE:]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        video_path = os.path.join(VIDEO_DIR, f"timelapse_last_{WINDOW_SIZE}_{timestamp}.mp4")
        
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-f', 'image2pipe', '-framerate', str(FPS_OUT),
            '-vcodec', 'mjpeg', '-i', '-', 
            '-vf', 'format=yuv420p',
            '-c:v', 'h264_v4l2m2m', 
            '-b:v', BITRATE, 
            video_path
        ]
        
        # WICHTIG: text=False, da wir Binärdaten (Bilder) senden
        process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        
        for fname in last_files:
            with open(fname, 'rb') as f:
                process.stdin.write(f.read())
        
        _, stderr = process.communicate()
        
        if process.returncode == 0:
            logging.info(f"Video erfolgreich erstellt: {video_path}")
        else:
            logging.error(f"FFmpeg Fehler: {stderr.decode('utf-8', errors='ignore')}")

    except Exception as e:
        logging.error(f"Allgemeiner Fehler beim Video-Rendering: {e}")

if __name__ == "__main__":
    try:
        # 1. Zeit-Check
        if not is_active_time():
            # Optional: Hier kein Fehler-Logging, da es "geplant" ist
            exit(0)

        # 2. Mount Check
        if not is_mounted(MOUNT_PATH):
            logging.error(f"Abbruch: {MOUNT_PATH} ist nicht gemountet!")
            exit(1)

        # 3. DIENSTE-CHECK (Wichtig nach Reboot/Stromausfall)
        check_and_stop_services()
        
        # 4. Kurze Pause für das Shelly-Licht (nur beim ersten Trigger der Startstunde)
        wait_for_light()
        
        os.makedirs(IMAGE_DIR, exist_ok=True)
        os.makedirs(VIDEO_DIR, exist_ok=True)

        # 2. Ablauf
        current_count = get_next_counter()
        if capture_image(current_count):
            save_counter(current_count)
            
            if current_count >= WINDOW_SIZE and current_count % MAX_IMAGES == 0:
                create_video()
                
    except Exception as e:
        logging.error(f"Unerwarteter Script-Fehler: {e}")