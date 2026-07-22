import os
import sys
import re
import json
import sqlite3
import subprocess
import threading
import time
import socket
import struct
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import gzip
import io
import concurrent.futures
import platform
import shutil
import inspect  # Ajouté pour permettre la détection dynamique et sécurisée de vos fonctions d'UI
import flet as ft
import requests
from ftplib import FTP
import asyncio
import tempfile

print("========================================")
print("Python :", sys.executable)
print("========================================")

try:
    import flet_video
    print("FLET_VIDEO CHARGÉ")
    print("Version :", getattr(flet_video, "__version__", "inconnue"))
    print("Fichier :", getattr(flet_video, "__file__", "inconnu"))
    HAS_FLET_VIDEO = True
    print("========================================")
except Exception as e:
    flet_video = None
    HAS_FLET_VIDEO = False
    print("ERREUR IMPORT FLET_VIDEO (Mode dégradé / WebView disponible)")
    print(repr(e))
    print("========================================")


# NOTE : 'import webview' a été supprimé. Pour afficher du web sur Android,
# utilise directement le composant natif de Flet : ft.WebView()

# ==========================================
# GESTION DES DÉPENDANCES
# ==========================================
# Plus de "subprocess pip install". Les modules sont importés directement
# car ils seront inclus directement dans l'APK grâce au requirements.txt.
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import paramiko
from smb.SMBConnection import SMBConnection

# ==========================================
# GESTION FLET-VIDEO (Remplacement de ft.Video déprécié)
# ==========================================
try:
    import flet_video
    FLET_VIDEO_AVAILABLE = True
except (ImportError, AttributeError):
    FLET_VIDEO_AVAILABLE = False


# ==========================================
# LECTEUR EXTERNE (SOLUTION DE SECOURS)
# ==========================================
def launch_external_player(file_path):
    """
    Lance le lecteur vidéo par défaut du système d'exploitation
    sans passer par la dépendance flet_video.
    """
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(file_path)
        elif system == "Darwin":  # macOS
            subprocess.run(["open", file_path])
        else:  # Linux / Autres
            subprocess.run(["xdg-open", file_path])
        print(f"[OmniCine] Vidéo lancée dans le lecteur externe : {file_path}")
    except Exception as e:
        print(f"[OmniCine] Erreur lancement lecteur externe : {e}")


# ==========================================
# GESTION MULTI-PLATEFORME DES CHEMINS
# ==========================================
def get_app_data_dir():
    """
    Retourne le répertoire de données sécurisé pour l'application
    compatible avec Windows, macOS, Linux et explicitement Android.
    """
    system = platform.system()
    
    # Détection spécifique d'Android (Flet définit souvent ces environnements)
    is_android = "ANDROID_ARGUMENT" in os.environ or os.environ.get("FLET_PLATFORM") == "android"
    
    if is_android:
        # Sur Android, os.path.expanduser("~") pointe vers la sandbox privée de l'application
        return os.path.join(os.path.expanduser("~"), "omnicine")
    
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return os.path.join(appdata, "OmniCine")
        else:
            return os.path.join(os.path.expanduser("~"), ".omnicine")
    
    elif system == "Darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "OmniCine")
    
    elif system == "Linux":
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            return os.path.join(xdg_data, "omnicine")
        else:
            return os.path.join(os.path.expanduser("~"), ".local", "share", "omnicine")
    
    else:
        return os.path.join(os.path.expanduser("~"), ".omnicine")

def ensure_directories():
    """
    Crée tous les répertoires nécessaires s'ils n'existent pas.
    """
    app_dir = get_app_data_dir()
    cache_dir = os.path.join(app_dir, "cache_images")
    trailers_dir = os.path.join(app_dir, "cache_trailers")
    
    for directory in [app_dir, cache_dir, trailers_dir]:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                print(f"[OmniCine] Erreur création répertoire {directory}: {e}")
    
    return app_dir, cache_dir, trailers_dir

# Initialisation des répertoires
APP_DATA_DIR, CACHE_DIR, TRAILERS_DIR = ensure_directories()
DB_NAME = os.path.join(APP_DATA_DIR, "omnicine.db")
CONFIG_NAME = os.path.join(APP_DATA_DIR, "config_omnicine.json")
CONFIG_BACKUP_NAME = os.path.join(APP_DATA_DIR, "config_omnicine.json.bak")

TRANSPARENT_PLACEHOLDER = "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

# ==========================================
# GESTION DE LA CONFIGURATION (config_omnicine.json)
# ==========================================
def load_config():
    """
    Charge la configuration depuis config_omnicine.json.
    Crée le fichier avec les valeurs par défaut s'il n'existe pas.
    """
    if os.path.exists(CONFIG_NAME):
        try:
            with open(CONFIG_NAME, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
                # Migration automatique des anciennes clés si nécessaire
                required_keys = {
                    "tmdb_api_key": "96ec18a95517ccee42b88a7bce3ffe8d",
                    "server_port": 8080,
                    "enable_server": True,
                    "ui_opacity": 0.55,
                    "gallery_opacity": 1.0,
                    "details_opacity": 0.60,
                    "buttons_opacity": 1.0,
                    "background_blur": 40,
                    "panel_blur": 20,
                    "fullscreen": False,
                    "big_picture": False,
                    "opensubtitles_api_key": "",
                    "opensubtitles_user_agent": "OmniCine",
                    "subtitle_accounts": {},
                    "preferred_player": "external",  # "native" pour ft.Video, "external" pour lecteurs externes
                    "auto_check_updates": True,
                    "subtitle_sources": ["subtitlecat", "opensubtitles"],  # Priorité des sources
                    "app_version": "1.0.0"
                }
                
                for key, default_value in required_keys.items():
                    if key not in config:
                        config[key] = default_value
                
                return config
        except Exception as e:
            print(f"[OmniCine] Erreur chargement config: {e}")
            # Tentative de restauration depuis backup
            if os.path.exists(CONFIG_BACKUP_NAME):
                try:
                    shutil.copy2(CONFIG_BACKUP_NAME, CONFIG_NAME)
                    return load_config()
                except Exception:
                    pass
    
    # Configuration par défaut
    default_config = {
        "app_name": "OmniCine",
        "tmdb_api_key": "96ec18a95517ccee42b88a7bce3ffe8d",
        "server_port": 8080,
        "enable_server": True,
        "players": {
            "MPV": "C:\\Program Files\\mpv\\mpv.exe" if platform.system() == "Windows" else "mpv",
            "VLC": "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe" if platform.system() == "Windows" else "vlc",
            "PotPlayer": "C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe" if platform.system() == "Windows" else "",
            "MPC-BE": "C:\\Program Files\\MPC-BE\\mpc-be64.exe" if platform.system() == "Windows" else ""
        },
        "ui_opacity": 0.55,
        "gallery_opacity": 1.0,
        "details_opacity": 0.60,
        "buttons_opacity": 1.0,
        "background_blur": 40,
        "panel_blur": 20,
        "fullscreen": False,
        "big_picture": False,
        "opensubtitles_api_key": "",
        "opensubtitles_user_agent": "OmniCine",
        "subtitle_accounts": {
            "opensubtitles": {
                "api_key": "",
                "username": "",
                "password": ""
            }
        },
        "preferred_player": "external",
        "auto_check_updates": True,
        "subtitle_sources": ["subtitlecat", "opensubtitles"],
        "app_version": "1.0.0"
    }
    
    save_config(default_config)
    return default_config

def save_config(config):
    """
    Sauvegarde la configuration dans config_omnicine.json.
    Crée automatiquement une sauvegarde (.bak) avant modification.
    """
    try:
        # Créer un backup si le fichier existe
        if os.path.exists(CONFIG_NAME):
            shutil.copy2(CONFIG_NAME, CONFIG_BACKUP_NAME)
        
        with open(CONFIG_NAME, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[OmniCine] Erreur sauvegarde config: {e}")

def restore_config_backup():
    """
    Restaure la configuration depuis le fichier .bak (Rollback).
    """
    if os.path.exists(CONFIG_BACKUP_NAME):
        try:
            shutil.copy2(CONFIG_BACKUP_NAME, CONFIG_NAME)
            return True
        except Exception as e:
            print(f"[OmniCine] Erreur restauration backup: {e}")
    return False

# ==========================================
# ISOLATION DES COMMANDES SPÉCIFIQUES WINDOWS
# ==========================================
def is_windows():
    """Vérifie si l'OS est Windows."""
    return platform.system() == "Windows"

def scan_network_via_netview():
    """
    Scan réseau via commande Windows 'net view'.
    Retourne une liste vide sur les autres plateformes.
    """
    if not is_windows():
        return {}
    
    shares_tree = {}
    try:
        output = subprocess.run(["net", "view"], capture_output=True, text=True, errors="ignore", timeout=10)
        computers = re.findall(r'\\\\([A-Za-z0-9\-_\.]+)', output.stdout)
        for comp in computers:
            shares_tree[comp] = []
            try:
                view_comp = subprocess.run(["net", "view", f"\\\\{comp}"], capture_output=True, text=True, errors="ignore", timeout=10)
                for line in view_comp.stdout.splitlines():
                    if "Disque" in line or "Disk" in line:
                        match = re.match(r'^([A-Za-z0-9\-_\s\$\x80-\xFF]+?)\s{2,}', line)
                        if match:
                            share_name = match.group(1).strip()
                            if not share_name.endswith('$'):
                                shares_tree[comp].append(share_name)
            except Exception:
                pass
    except Exception:
        pass
    return shares_tree

# ==========================================
# SYSTÈME DE MISE À JOUR MULTI-PLATEFORME
# ==========================================
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/kixx-fr/omnicine/main/version.json"

def get_current_platform():
    """Retourne l'identifiant de plateforme actuel."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Darwin":
        # Distinguer macOS (Intel) et macOS (ARM)
        machine = platform.machine()
        return "macos_arm" if machine in ["arm64", "aarch64"] else "macos"
    elif system == "Linux":
        # Détecter Android via environnement
        if "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ:
            return "android"
        return "linux"
    else:
        return "unknown"

async def check_for_updates():
    """
    Vérifie les mises à jour disponibles depuis le manifeste distant.
    Retourne (update_available, version_info, download_url).
    """
    try:
        response = requests.get(UPDATE_MANIFEST_URL, timeout=5)
        if response.status_code == 200:
            manifest = response.json()
            current_platform = get_current_platform()
            current_version = load_config().get("app_version", "1.0.0")
            
            if current_platform in manifest:
                platform_info = manifest[current_platform]
                latest_version = platform_info.get("version", "1.0.0")
                
                # Comparaison simple de versions (assumption: format X.Y.Z)
                if latest_version > current_version:
                    return True, {
                        "current_version": current_version,
                        "latest_version": latest_version,
                        "platform": current_platform,
                        "download_url": platform_info.get("download_url", ""),
                        "changelog": platform_info.get("changelog", ""),
                        "previous_version": platform_info.get("previous_version", current_version),
                        "previous_url": platform_info.get("previous_download_url", "")
                    }
    except Exception as e:
        print(f"[OmniCine] Erreur vérification mises à jour: {e}")
    
    return False, None

def get_rollback_info():
    """
    Retourne les informations pour rollback vers la version précédente.
    """
    try:
        response = requests.get(UPDATE_MANIFEST_URL, timeout=5)
        if response.status_code == 200:
            manifest = response.json()
            current_platform = get_current_platform()
            if current_platform in manifest:
                platform_info = manifest[current_platform]
                return {
                    "previous_version": platform_info.get("previous_version", ""),
                    "previous_url": platform_info.get("previous_download_url", "")
                }
    except Exception:
        pass
    return None

# ==========================================
# 1. GESTION DE LA BASE DE DONNÉES
# ==========================================
def init_db():
    """Initialise la base de données SQLite avec toutes les tables nécessaires."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE,
            filename TEXT,
            title TEXT,
            year TEXT,
            synopsis TEXT,
            rating TEXT,
            poster_path TEXT,
            backdrop_path TEXT,
            cast_info TEXT,
            director TEXT,
            genres TEXT,
            runtime TEXT,
            actors TEXT,
            country TEXT,
            origin_server TEXT DEFAULT 'local',
            tmdb_id INTEGER,
            imdb_id TEXT
        )
    ''')
    
    # Ajout des colonnes si elles n'existent pas (migration)
    columns_to_check = [
        ("director", "TEXT"), ("genres", "TEXT"), ("runtime", "TEXT"), 
        ("actors", "TEXT"), ("country", "TEXT"), ("origin_server", "TEXT DEFAULT 'local'"),
        ("tmdb_id", "INTEGER"), ("imdb_id", "TEXT")
    ]
    for col, col_type in columns_to_check:
        try:
            cursor.execute(f"ALTER TABLE videos ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scanned_folders (
            folder_path TEXT UNIQUE
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS network_storages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            type TEXT, 
            host TEXT,
            user TEXT,
            password TEXT,
            root_path TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS federated_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            url TEXT UNIQUE
        )
    ''')
    
    conn.commit()
    conn.close()

# ==========================================
# 2. ALGORITHME DE NETTOYAGE ET RECHERCHE TMDB
# ==========================================
def clean_filename(filename):
    """Nettoie le nom de fichier pour extraire le titre et l'année."""
    name, _ = os.path.splitext(filename)
    name = re.sub(r'[._\-\[\]()]', ' ', name)
    year_match = re.search(r'\b(19|20)\d{2}\b', name)
    year = year_match.group(0) if year_match else ""
    if year:
        name = name.split(year)[0]
    
    tags = [
        r'\b1080p\b', r'\b720p\b', r'\b2160p\b', r'\b4k\b', r'\buhd\b',
        r'\bbluray\b', r'\bremux\b', r'\bbdrip\b', r'\bdvdrip\b',
        r'\bx264\b', r'\bx265\b', r'\bh264\b', r'\bh265\b', r'\bhevc\b',
        r'\bmulti\b', r'\bvostfr\b', r'\bfrench\b', r'\btruefrench\b',
        r'\bsubfrench\b', r'\bproper\b', r'\brepack\b', r'\bweb-dl\b',
        r'\bwebrip\b', r'\baac\b', r'\bdd5\.1\b', r'\bdts\b', r'\bac3\b'
    ]
    for tag in tags:
        name = re.sub(tag, '', name, flags=re.IGNORECASE)
    
    return ' '.join(name.split()).strip(), year

def fetch_metadata(title, year="", api_key=None):
    """Récupère les métadonnées depuis l'API TMDB."""
    config = load_config()
    if not api_key:
        api_key = config.get("tmdb_api_key", "96ec18a95517ccee42b88a7bce3ffe8d")
    
    encoded_title = requests.utils.quote(title)
    search_url = f"https://api.themoviedb.org/3/search/multi?api_key={api_key}&query={encoded_title}&language=fr-FR"
    
    try:
        response = requests.get(search_url, timeout=6)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                best_match = results[0]
                if year:
                    for item in results:
                        date_str = item.get("release_date") or item.get("first_air_date") or ""
                        if year in date_str:
                            best_match = item
                            break
                
                media_type = best_match.get("media_type", "movie")
                media_id = best_match.get("id")
                details_url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={api_key}&language=fr-FR&append_to_response=credits,videos"
                details_resp = requests.get(details_url, timeout=6)
                
                if details_resp.status_code == 200:
                    details = details_resp.json()
                    display_title = details.get("title") or details.get("name") or title
                    date_out = details.get("release_date") or details.get("first_air_date") or ""
                    movie_year = date_out.split("-")[0] if "-" in date_out else year
                    synopsis = details.get("overview") or "Aucun résumé disponible sur TMDB."
                    vote_avg = details.get("vote_average", "N/A")
                    rating = f"{vote_avg}/10" if vote_avg != "N/A" else "N/A"
                    
                    p_path = details.get("poster_path")
                    b_path = details.get("backdrop_path")
                    poster = f"https://image.tmdb.org/t/p/w500{p_path}" if p_path else ""
                    backdrop = f"https://image.tmdb.org/t/p/w1280{b_path}" if b_path else ""
                    
                    credits = details.get("credits", {})
                    cast_nodes = credits.get("cast", [])[:6]
                    actors_list = [member.get("name") for member in cast_nodes]
                    actors_str = ", ".join(actors_list) if actors_list else "Inconnu"
                    
                    crew_nodes = credits.get("crew", [])
                    directors = [m.get("name") for m in crew_nodes if m.get("job") == "Director"]
                    director_str = ", ".join(directors) if directors else "Inconnu"
                    
                    genres_list = [g.get("name") for g in details.get("genres", [])]
                    genres_str = ", ".join(genres_list) if genres_list else "N/A"
                    
                    runtime = details.get("runtime") or (details.get("episode_run_time")[0] if details.get("episode_run_time") else "N/A")
                    runtime_str = f"{runtime} min" if isinstance(runtime, int) else "N/A"
                    
                    countries = [c.get("name") for c in details.get("production_countries", [])]
                    country_str = ", ".join(countries) if countries else "N/A"
                    
                    # Récupération des IDs pour les sous-titres
                    tmdb_id = details.get("id")
                    imdb_id = details.get("imdb_id")
                    
                    # Récupération des bandes-annonces
                    videos_data = details.get("videos", {})
                    trailers = []
                    if videos_data and videos_data.get("results"):
                        for vid in videos_data["results"]:
                            if vid.get("type", "").lower() == "trailer" and vid.get("site", "").lower() == "youtube":
                                trailers.append({
                                    "name": vid.get("name", ""),
                                    "key": vid.get("key", ""),
                                    "url": f"https://www.youtube.com/watch?v={vid.get('key', '')}"
                                })
                    
                    return {
                        "title": display_title, "year": movie_year, "synopsis": synopsis, "rating": rating,
                        "poster": poster, "backdrop": backdrop, "cast": f"Casting: {actors_str}",
                        "director": director_str, "genres": genres_str, "runtime": runtime_str,
                        "actors": actors_str, "country": country_str,
                        "tmdb_id": tmdb_id, "imdb_id": imdb_id, "trailers": trailers
                    }
    except Exception as e:
        print(f"[OmniCine] Erreur fetch metadata: {e}")
        
    return {
        "title": title, "year": year, "synopsis": "Fichier multimédia indexé sans métadonnées en ligne.",
        "rating": "N/A", "poster": "", "backdrop": "", "cast": "Production: Locale",
        "director": "Inconnu", "genres": "N/A", "runtime": "N/A", "actors": "Inconnu", "country": "N/A",
        "tmdb_id": None, "imdb_id": None, "trailers": []
    }

def download_image(url, filename_prefix):
    """Télécharge et met en cache une image depuis une URL."""
    if not url: return ""
    try:
        ext = url.split('.')[-1].split('?')[0]
        if len(ext) > 4 or not ext: ext = "jpg"
        clean_prefix = re.sub(r'[\\/*?:"<>|]', "_", filename_prefix)
        filepath = os.path.join(CACHE_DIR, f"{clean_prefix}.{ext}")
        if os.path.exists(filepath): return filepath
        
        r = requests.get(url, stream=True, timeout=8)
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
            return filepath
    except Exception as e:
        print(f"[OmniCine] Erreur download image: {e}")
    return ""

# ==========================================
# 3. INTERCONNEXIONS ET EXPLORATION RÉSEAU
# ==========================================
def get_remote_ftp_files(host, user, password, root_path):
    """Récupère la liste des fichiers depuis un serveur FTP."""
    file_list = []
    try:
        ftp = FTP(host, timeout=5)
        ftp.login(user, password)
        ftp.cwd(root_path)
        def process_lines(line):
            parts = line.split(None, 8)
            if len(parts) == 9:
                filename = parts[8]
                if filename.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm')):
                    file_list.append(filename)
        ftp.retrlines('LIST', process_lines)
        ftp.quit()
    except Exception as e:
        print(f"[OmniCine] Erreur FTP: {e}")
    return file_list

def get_remote_sftp_files(host, user, password, root_path):
    """Récupère la liste des fichiers depuis un serveur SFTP."""
    file_list = []
    try:
        transport = paramiko.Transport((host, 22))
        transport.connect(username=user, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        for entry in sftp.listdir_attr(root_path):
            filename = entry.filename
            if filename.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm')):
                file_list.append(filename)
        sftp.close()
        transport.close()
    except Exception as e:
        print(f"[OmniCine] Erreur SFTP: {e}")
    return file_list

# ==========================================
# 4. EXÉCUTION DES LECTEURS VIDÉO
# ==========================================
def launch_video_process(player_executable, video_path, page_instance, video_id=None):
    """Lance un lecteur vidéo externe via subprocess."""
    def run():
        try:
            try: page_instance.window.minimized = True
            except Exception: pass
            page_instance.update()
            cmd = [player_executable, video_path]
            exe_basename = os.path.basename(player_executable).lower()
            
            if not video_path.startswith("http"):
                base_path, _ = os.path.splitext(video_path)
                srt_candidates = [f"{base_path}.srt", f"{base_path}.fr.srt", f"{base_path}.french.srt"]
                active_sub = next((c for c in srt_candidates if os.path.exists(c)), None)
                if active_sub:
                    if "mpv" in exe_basename or "vlc" in exe_basename: cmd.append(f"--sub-file={active_sub}")
                    elif "mpc" in exe_basename: cmd.extend(["/sub", active_sub])
                    elif "potplayer" in exe_basename: cmd.append(active_sub)
            subprocess.run(cmd, check=True)
        except Exception as e:
            print(f"[OmniCine] Erreur lecteur externe: {e}")
        finally:
            time.sleep(0.5)
            try: page_instance.window.minimized = False
            except Exception: pass
            page_instance.update()
            
            # Déclenchement via notre pont asynchrone sécurisé
            if video_id is not None and not video_path.startswith("http") and os.path.exists(video_path):
                try:
                    page_instance.run_task(run_clean_async, video_path, video_id)
                except Exception as err:
                    print(f"[OmniCine] Erreur lancement nettoyage externe : {err}")
                    
    threading.Thread(target=run, daemon=True).start()

# ==========================================
# 5. SERVEUR FASTAPI INTÉGRÉ & STREAMING
# ==========================================
server_app = FastAPI(title="OmniCine Core Server")
server_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Configuration du dossier temporaire pour les trailers téléchargés
TRAILERS_DIR = "cache_trailers"
if not os.path.exists(TRAILERS_DIR):
    os.makedirs(TRAILERS_DIR)

@server_app.get("/library")
def get_api_library():
    """API: Retourne la bibliothèque locale."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, year, filepath, synopsis, rating, cast_info, 
               poster_path, backdrop_path, director, genres, runtime, actors, country 
        FROM videos WHERE origin_server = 'local'
    """)
    rows = cursor.fetchall()
    conn.close()
    return [{
        "id": r[0], "title": r[1], "year": r[2], "filepath": r[3], "synopsis": r[4], "rating": r[5], "cast": r[6],
        "poster_path": r[7], "backdrop_path": r[8], "director": r[9], "genres": r[10], "runtime": r[11], "actors": r[12], "country": r[13]
    } for r in rows]

@server_app.get("/poster/{movie_id}")
def get_api_poster(movie_id: int):
    """API: Retourne l'affiche d'un film."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT poster_path FROM videos WHERE id=?", (movie_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] and os.path.exists(row[0]): return FileResponse(row[0])
    raise HTTPException(status_code=404)

@server_app.get("/backdrop/{movie_id}")
def get_api_backdrop(movie_id: int):
    """API: Retourne le backdrop d'un film."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT backdrop_path FROM videos WHERE id=?", (movie_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] and os.path.exists(row[0]): return FileResponse(row[0])
    raise HTTPException(status_code=404)

def range_video_sender(filepath: str, start: int, end: int, chunk_size: int = 1024*1024):
    """Générateur pour le streaming vidéo avec support Range."""
    with open(filepath, "rb") as video_file:
        video_file.seek(start)
        bytes_to_send = end - start + 1
        while bytes_to_send > 0:
            data = video_file.read(min(chunk_size, bytes_to_send))
            if not data: break
            bytes_to_send -= len(data)
            yield data

@server_app.get("/stream/{movie_id}")
def stream_api_video(movie_id: int, request: Request, range: str = Header(None)):
    """API: Stream vidéo avec support HTTP Range."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT filepath FROM videos WHERE id=?", (movie_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not os.path.exists(row[0]): raise HTTPException(status_code=404)
    
    filepath = row[0]
    file_size = os.path.getsize(filepath)
    start, end = 0, file_size - 1
    if range:
        range_match = re.match(r"bytes=(\d+)-(\d*)", range)
        if range_match:
            start = int(range_match.group(1))
            if range_match.group(2): end = int(range_match.group(2))
                
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}", "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1), "Content-Type": "video/mp4" if filepath.lower().endswith('.mp4') else "video/x-matroska"
    }
    return StreamingResponse(range_video_sender(filepath, start, end), status_code=206, headers=headers)

@server_app.get("/stream/trailer/{filename}")
def stream_api_trailer(filename: str, request: Request, range: str = Header(None)):
    """API: Stream une bande-annonce téléchargée localement avec support HTTP Range."""
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(TRAILERS_DIR, safe_filename)
    if not os.path.exists(filepath): 
        raise HTTPException(status_code=404, detail="Fichier de bande-annonce introuvable.")
    
    file_size = os.path.getsize(filepath)
    start, end = 0, file_size - 1
    if range:
        range_match = re.match(r"bytes=(\d+)-(\d*)", range)
        if range_match:
            start = int(range_match.group(1))
            if range_match.group(2): end = int(range_match.group(2))
                
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}", "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1), "Content-Type": "video/mp4"
    }
    return StreamingResponse(range_video_sender(filepath, start, end), status_code=206, headers=headers)

@server_app.get("/trailer/delete/{filename}")
def delete_api_trailer(filename: str):
    """API: Supprime physiquement la bande-annonce du stockage pour éviter la saturation."""
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(TRAILERS_DIR, safe_filename)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            return {"status": "success", "message": f"Fichier {safe_filename} nettoyé avec succès."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur d'effacement : {str(e)}")
    raise HTTPException(status_code=404, detail="Fichier introuvable.")

def is_port_in_use(port):
    """Vérifie si un port est déjà utilisé."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def start_fastapi(port=8080):
    """Démarre le serveur FastAPI en arrière-plan."""
    if is_port_in_use(port):
        print(f"[OmniCine] Note : Le port {port} est déjà utilisé.")
        return
    try: uvicorn.run(server_app, host="0.0.0.0", port=port, log_level="warning")
    except Exception as e: print(f"[OmniCine] Erreur serveur: {e}")

# ==========================================
# 6. MODULE DE BANDES-ANNONCES (TRAILERS)
# ==========================================

def get_youtube_embed_url(youtube_key):
    r"""
    Convertit une clé YouTube en URL embed.
    
    [!] ATTENTION : Incompatible avec le composant natif ft.Video (Flet 0.27.6).
    Cette URL charge une page HTML complexe et doit être exclusivement réservée 
    à un composant ft.WebView.
    """
    return f"https://www.youtube.com/embed/{youtube_key}?autoplay=1&rel=0"


def get_trailer_stream_url(youtube_key):
    """
    Génère l'URL de visionnage standard YouTube ou locale.
    
    Correction Flet 0.27.6 / FastAPI : Pour éviter le repli automatique en 360p causé 
    par le moteur interne de Flet sur les liens YouTube directs, cette fonction inspecte 
    le cache local à la recherche d'une version haute définition préalablement récupérée.
    Si elle existe, le flux est redirigé vers le serveur de streaming FastAPI local.
    """
    if not youtube_key:
        return ""
        
    # Analyse du répertoire de cache pour intercepter la version HD téléchargée
    cache_dir = "cache_trailers"
    if os.path.exists(cache_dir):
        for filename in os.listdir(cache_dir):
            # Si le nom de fichier contient l'identifiant YouTube unique du trailer
            if youtube_key in filename and (filename.endswith(".mp4") or filename.endswith(".mkv")):
                # On sert le fichier HD via la passerelle de streaming locale FastAPI
                return f"http://127.0.0.1:8080/stream/trailer/{filename}"
                
    # Sécurité : Si le téléchargement n'a pas encore eu lieu, retour à l'URL brute
    return f"https://www.youtube.com/watch?v={youtube_key}"
# ==========================================
# 7. MOTEUR DE SOUS-TITRES MULTI-SOURCE (PRIORITÉ GRATUITE + FALLBACK)
# ==========================================
SUBTITLE_LANGUAGES = [
    ("fr", "🇫🇷 Français"), ("en", "🇬🇧 Anglais"), ("es", "🇪🇸 Espagnol"),
    ("de", "🇩🇪 Allemand"), ("it", "🇮🇹 Italien"), ("pt", "🇵🇹 Portugais"),
    ("nl", "🇳🇱 Néerlandais"), ("pl", "🇵🇱 Polonais"), ("ru", "🇷🇺 Russe"),
    ("tr", "🇹🇷 Turc"), ("zh", "🇨🇳 Chinois"), ("ja", "🇯🇵 Japonais"),
    ("ko", "🇰🇷 Coréen"), ("ar", "🇸🇦 Arabe")
]

ISO_TO_OPENSUBTITLES = {"fr": "fre", "en": "eng", "es": "spa", "de": "ger", "it": "ita", "pt": "por", "nl": "dut", "pl": "pol", "ru": "rus", "tr": "tur", "zh": "chi", "ja": "jpn", "ko": "kor", "ar": "ara"}

class SmartSubtitleEngine:
    """
    Moteur de sous-titres intelligent avec priorité aux services gratuits
    et fallback automatique vers OpenSubtitles.
    """
    def __init__(self, api_key_tmdb, account_config, subtitle_sources=None):
        self.api_key_tmdb = api_key_tmdb
        self.account_config = account_config
        self.subtitle_sources = subtitle_sources or ["subtitlecat", "opensubtitles"]
        self.logs = []
        
    def log(self, srv, msg):
        self.logs.append(f"[{srv}] {msg}")
        print(f"[OmniCine SubEngine] [{srv}] {msg}")

    def get_moviehash(self, filepath):
        """Calcule le hash OpenSubtitles d'un fichier vidéo."""
        try:
            if not filepath or not os.path.exists(filepath): return None, None
            longlongformat = '<q'
            bytesize = struct.calcsize(longlongformat)
            filesize = os.path.getsize(filepath)
            hash_val = filesize
            if filesize < 65536 * 2: return None, None
            with open(filepath, "rb") as f:
                for _ in range(65536 // bytesize):
                    buffer = f.read(bytesize)
                    (l_value,) = struct.unpack(longlongformat, buffer)
                    hash_val = (hash_val + l_value) & 0xFFFFFFFFFFFFFFFF
                f.seek(filesize - 65536, 0)
                for _ in range(65536 // bytesize):
                    buffer = f.read(bytesize)
                    (l_value,) = struct.unpack(longlongformat, buffer)
                    hash_val = (hash_val + l_value) & 0xFFFFFFFFFFFFFFFF
            return "%016x" % hash_val, str(filesize)
        except Exception as e:
            self.log("HASH", str(e))
            return None, None

    def extract_tags(self, filename):
        """Extrait les tags de qualité/codec du nom de fichier."""
        tags = re.findall(r'(1080p|720p|2160p|4k|bluray|webrip|web-dl|hdrip|remux|x264|x265|hevc|multi|vostfr|truefrench|extended|director)', filename, re.IGNORECASE)
        return [t.lower() for t in tags]

    def get_tmdb_advanced_info(self, title, year):
        """Récupère les informations avancées TMDB (IMDb ID, TMDB ID)."""
        meta = {"imdb_id": None, "original_title": title, "tmdb_id": None}
        if not getattr(self, "api_key_tmdb", None): return meta
        try:
            search_url = f"https://api.themoviedb.org/3/search/movie?api_key={self.api_key_tmdb}&query={urllib.parse.quote(title)}&language=en-US"
            resp = requests.get(search_url, timeout=5)
            if resp.status_code == 200 and resp.json().get("results"):
                best = resp.json()["results"][0]
                meta["tmdb_id"] = best.get("id")
                meta["original_title"] = best.get("original_title", title)
                
                if meta["tmdb_id"]:
                    details_url = f"https://api.themoviedb.org/3/movie/{meta['tmdb_id']}?api_key={self.api_key_tmdb}"
                    det_resp = requests.get(details_url, timeout=5)
                    if det_resp.status_code == 200:
                        meta["imdb_id"] = det_resp.json().get("imdb_id")
        except Exception as e: self.log("TMDB", f"Erreur fetch {e}")
        return meta

    def search_subtitlecat(self, meta, lang):
        """Recherche sur SubtitleCat (service gratuit, anonyme)."""
        results = []
        try:
            query = f"{meta['original_title']} {lang}"
            url = f"https://www.subtitlecat.com/index.php?search={urllib.parse.quote(query)}"
            resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                links = re.findall(r'<a href="([^"]+)">([^<]+)</a>', resp.text)
                for href, txt in links:
                    if "subtitles/" in href and lang.lower() in txt.lower() and meta['original_title'].lower() in txt.lower():
                        dl_id = href.split('/')[-1].replace('.html', '')
                        results.append({
                            "service": "SubtitleCat",
                            "file_name": txt.strip(),
                            "release": "WEBRip/HD",
                            "downloads": 100,
                            "rating": 0,
                            "direct_url": f"https://www.subtitlecat.com/subs/{dl_id}"
                        })
                        if len(results) >= 3: break
            else: self.log("SubtitleCat", f"HTTP {resp.status_code}")
        except Exception as e: self.log("SubtitleCat", f"Exception: {e}")
        return results

    def search_opensubtitles(self, meta, mhash, msize, lang, filename):
        """Recherche sur OpenSubtitles (fallback)."""
        results = []
        os_lang = ISO_TO_OPENSUBTITLES.get(lang, lang)
        
        # Correction du code langue pour l'API REST v1
        lang_str = str(os_lang).lower().strip()
        lang_map = {
            "fre": "fr", "fra": "fr", "french": "fr",
            "eng": "en", "english": "en",
            "spa": "es", "spanish": "es",
            "ger": "de", "deu": "de", "german": "de",
            "ita": "it", "italian": "it"
        }
        os_lang = lang_map.get(lang_str, lang_str[:2])
        
        os_cfg = self.account_config.get("opensubtitles", {})
        api_key = os_cfg.get("api_key") or "zHsTcaQlOAQdVkabXOqqKOXd56NTlGk9"
        user_agent = self.account_config.get("user_agent", "OmniCine")
        token = None
        
        self.log("OpenSubtitles", f"API Key présente: {bool(api_key)}")
        
        try:
            headers = {
                "Api-Key": api_key, 
                "User-Agent": user_agent,
                "Accept": "application/json"
            }
            
            # Authentification si identifiants fournis
            if os_cfg.get("username") and os_cfg.get("password"):
                try:
                    auth_resp = requests.post("https://api.opensubtitles.com/api/v1/login", 
                        json={"username": os_cfg["username"], "password": os_cfg["password"]}, 
                        headers=headers, timeout=5)
                    if auth_resp.status_code == 200:
                        token = auth_resp.json().get("token")
                        headers["Authorization"] = f"Bearer {token}"
                except Exception as e:
                    self.log("OpenSubtitles", f"Login ignoré : {e}")

            data = []

            # Recherche par moviehash
            if mhash:
                params = {"languages": os_lang, "moviehash": mhash}
                try:
                    resp = requests.get("https://api.opensubtitles.com/api/v1/subtitles", headers=headers, params=params, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                except Exception as e:
                    self.log("OpenSubtitles", f"Erreur hash : {e}")

            # Fallback IMDb ID
            if not data and meta and meta.get("imdb_id"):
                try:
                    imdb_val = str(meta["imdb_id"]).lower().replace("tt", "").strip()
                    if imdb_val.isdigit():
                        params = {"languages": os_lang, "imdb_id": int(imdb_val)}
                        resp = requests.get("https://api.opensubtitles.com/api/v1/subtitles", headers=headers, params=params, timeout=10)
                        if resp.status_code == 200:
                            data = resp.json().get("data", [])
                except Exception as e:
                    self.log("OpenSubtitles", f"Erreur IMDb : {e}")

            # Fallback TMDB ID
            if not data and meta and meta.get("tmdb_id"):
                try:
                    params = {"languages": os_lang, "tmdb_id": int(meta["tmdb_id"])}
                    resp = requests.get("https://api.opensubtitles.com/api/v1/subtitles", headers=headers, params=params, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                except Exception as e:
                    self.log("OpenSubtitles", f"Erreur TMDB : {e}")

            # Fallback recherche textuelle
            if not data:
                search_query = meta.get("original_title")
                if not search_query or search_query == filename:
                    name, _ = os.path.splitext(filename)
                    name = re.sub(r'[._\-\[\]()]', ' ', name)
                    year_match = re.search(r'\b(19|20)\d{2}\b', name)
                    if year_match:
                        name = name.split(year_match.group(0))[0]
                    tags = [
                        r'\b1080p\b', r'\b720p\b', r'\b2160p\b', r'\b4k\b', r'\buhd\b',
                        r'\bbluray\b', r'\bremux\b', r'\bbdrip\b', r'\bdvdrip\b',
                        r'\bx264\b', r'\bx265\b', r'\bh264\b', r'\bh265\b', r'\bhevc\b',
                        r'\bmulti\b', r'\bvostfr\b', r'\bfrench\b', r'\btruefrench\b',
                        r'\bsubfrench\b', r'\bproper\b', r'\brepack\b', r'\bweb-dl\b',
                        r'\bwebrip\b', r'\baac\b', r'\bdd5\.1\b', r'\bdts\b', r'\bac3\b'
                    ]
                    for tag in tags:
                        name = re.sub(tag, '', name, flags=re.IGNORECASE)
                    search_query = ' '.join(name.split()).strip()

                params = {"languages": os_lang, "query": search_query}
                try:
                    resp = requests.get("https://api.opensubtitles.com/api/v1/subtitles", headers=headers, params=params, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                except Exception as e:
                    self.log("OpenSubtitles", f"Erreur texte : {e}")

            # Traitement des résultats
            if data:
                for item in data:
                    attrs = item.get("attributes", {})
                    release_name = attrs.get("release") or ""
                    files = attrs.get("files", [])
                    if not files:
                        continue
                    
                    file_id = files[0].get("file_id")
                    file_name = files[0].get("file_name") or release_name or "Subtitle.srt"
                    downloads = attrs.get("download_count", 0)
                    rating = attrs.get("ratings", 0)
                    
                    results.append({
                        "service": "OpenSubtitles",
                        "file_name": file_name,
                        "release": release_name,
                        "downloads": downloads,
                        "rating": rating,
                        "file_id": file_id,
                        "_api_key": api_key,
                        "_token": token
                    })

        except Exception as e: 
            self.log("OpenSubtitles", f"Erreur critique : {e}")

        return results

    def resolve_url(self, res):
        """Résout l'URL de téléchargement d'un sous-titre."""
        if res.get("direct_url"): return res["direct_url"]
        if res["service"] == "OpenSubtitles" and res.get("file_id"):
            user_agent = self.account_config.get("user_agent", "OmniCine")
            headers = {
                "Api-Key": res["_api_key"], 
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            if res.get("_token"): headers["Authorization"] = f"Bearer {res['_token']}"
            try:
                resp = requests.post("https://api.opensubtitles.com/api/v1/download", json={"file_id": res["file_id"]}, headers=headers, timeout=10)
                if resp.status_code == 200: return resp.json().get("link")
            except Exception as e: 
                self.log("OpenSubtitles Download", f"Exception: {e}")
        return None

    def search_all(self, filepath, title, year, lang):
        """
        Recherche sur toutes les sources configurées avec priorité.
        Exécute d'abord les services gratuits, puis fallback vers OpenSubtitles.
        """
        self.logs.clear()
        filename = os.path.basename(filepath) if filepath else title
        
        try:
            mhash, msize = self.get_moviehash(filepath)
        except Exception as e:
            self.log("SubEngine", f"Erreur calcul hash : {str(e)}")
            mhash, msize = None, None

        meta = self.get_tmdb_advanced_info(title, year)
        file_tags = self.extract_tags(filename)
        
        all_results = []
        
        # Exécution séquentielle selon la priorité configurée
        for source in self.subtitle_sources:
            if source == "subtitlecat":
                results = self.search_subtitlecat(meta, lang)
                if results:
                    self.log("SubtitleCat", f"{len(results)} résultats trouvés")
                    all_results.extend(results)
                    # Si on trouve des résultats gratuits, on peut s'arrêter (optionnel)
                    # break  # Commenté pour continuer avec OpenSubtitles si configuré
                    
            elif source == "opensubtitles":
                results = self.search_opensubtitles(meta, mhash, msize, lang, filename)
                if results:
                    self.log("OpenSubtitles", f"{len(results)} résultats trouvés")
                    all_results.extend(results)
        
        # Déduplication et Tri
        unique = {}
        for r in all_results:
            k = r["service"] + r["file_name"]
            if k not in unique: unique[k] = r
        
        final_list = list(unique.values())
        for r in final_list:
            score = r.get("downloads", 0) / 1000.0
            if mhash and mhash in r.get("release", ""): score += 100
            rel_tags = self.extract_tags(r.get("release", "") + r.get("file_name", ""))
            for t in file_tags:
                if t in rel_tags: score += 10
            r["_score"] = score
                
        final_list.sort(key=lambda x: x["_score"], reverse=True)
        return final_list

    def download_and_extract(self, result, destination, base_name, lang_code):
        """Télécharge et extrait le fichier de sous-titres."""
        try:
            url = self.resolve_url(result)
            if not url: return None, "Résolution URL impossible"
            
            resp = requests.get(url, timeout=15, stream=True)
            if resp.status_code != 200: return None, f"HTTP {resp.status_code}"
            
            raw_data = resp.content
            ext = ".srt"
            
            if b'PK\x03\x04' in raw_data[:4] or url.endswith('.zip'):
                ext = ".srt"
                with zipfile.ZipFile(io.BytesIO(raw_data)) as zf:
                    srt_files = [f for f in zf.namelist() if f.lower().endswith(('.srt', '.ass', '.vtt', '.sub'))]
                    if not srt_files: return None, "Aucun sous-titre dans le ZIP"
                    raw_data = zf.read(srt_files[0])
                    ext = os.path.splitext(srt_files[0])[1]
            elif b'\x1f\x8b' in raw_data[:2] or url.endswith('.gz'):
                raw_data = gzip.decompress(raw_data)
            else:
                for candidate in [".srt", ".ass", ".vtt", ".sub"]:
                    if url.lower().endswith(candidate): ext = candidate
            
            # Conversion ASS/VTT -> SRT
            if ext.lower() in [".vtt", ".ass"]:
                try:
                    txt = raw_data.decode('utf-8', errors='ignore')
                    txt = re.sub(r'WEBVTT\n\n', '', txt)
                    txt = re.sub(r'\{[^}]+\}', '', txt)
                    raw_data = txt.encode('utf-8')
                    ext = ".srt"
                except: pass
            
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", base_name)
            out_path = os.path.join(destination, f"{safe_name}.{lang_code}{ext}")
            with open(out_path, "wb") as f: f.write(raw_data)
            return out_path, None
        except Exception as e:
            return None, str(e)

# ==========================================
# 8. INTERFACE FLET PRINCIPALE
# ==========================================
def main(page: ft.Page):
    """Fonction principale de l'application Flet."""
    init_db()
    config = load_config()
    page.title = config.get("app_name", "OmniCine")
    try: page.theme_mode = ft.ThemeMode.DARK
    except Exception: page.theme_mode = "dark"
    page.padding = 0
    
    page.window.full_screen = config.get("fullscreen", False)
    
    current_selected_video = None
    current_library_source = "local"
    current_trailers = []
    
    # Démarrage du serveur FastAPI si activé
    if config.get("enable_server", True):
        threading.Thread(target=lambda: start_fastapi(port=config.get("server_port", 8080)), daemon=True).start()

    def show_snackbar(message, color="#E50914"):
        """Affiche un message snackbar."""
        page.snack_bar = ft.SnackBar(ft.Text(message, color="white"), bgcolor=color, open=True)
        page.update()

    # ==========================================
    # FONCTIONS D'ACCÈS AUX DONNÉES
    # ==========================================
    def get_all_local_videos():
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, year, filepath, synopsis, rating, cast_info, poster_path, backdrop_path, director, genres, runtime, actors, country, tmdb_id, imdb_id FROM videos WHERE origin_server = 'local'")
        rows = cursor.fetchall()
        conn.close()
        return rows
        
    def get_scanned_folders():
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT folder_path FROM scanned_folders")
        rows = [r[0] for r in cursor.fetchall()]
        conn.close()
        return rows

    def get_network_storages():
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, type, host, user, password, root_path")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_network_storages_full():
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, type, host, user, password, root_path FROM network_storages")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_federated_servers():
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, url FROM federated_servers")
        rows = cursor.fetchall()
        conn.close()
        return rows

    def create_help_box(text, example):
        """Crée une boîte d'aide visuelle."""
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon("info_outline", color="#E50914", size=16),
                    ft.Text(text, size=13, color="grey400", weight="medium")
                ], spacing=8),
                ft.Text(example, size=12, color="grey500", italic=True)
            ], spacing=4),
            padding=ft.padding.only(left=10, top=5, bottom=5),
            border=ft.border.Border(left=ft.border.BorderSide(3, "#E50914"))
        )

    # ==========================================
    # FONCTIONS DE NETTOYAGE SÉCURISÉ (PARACHUTES)
    # ==========================================
    def safe_show_snackbar(message):
        try: show_snackbar(message)
        except Exception:
            try: page.open(ft.SnackBar(ft.Text(message)))
            except Exception: print(message)

    def proposer_nettoyage_hybride(video_path, video_id):
        import os, shutil, sqlite3, inspect
        if not video_path or not os.path.exists(video_path): return

        dossier, nom_fichier = os.path.split(video_path)
        nom_sans_ext, _ = os.path.splitext(nom_fichier)
        fichiers_a_supprimer = [video_path]
        if dossier and os.path.exists(dossier):
            for f in os.listdir(dossier):
                if f.startswith(nom_sans_ext) and any(f.lower().endswith(ext) for ext in [".srt", ".vtt", ".ass", ".sub", ".idx"]):
                    fichiers_a_supprimer.append(os.path.join(dossier, f))

        taille_go = sum(os.path.getsize(f) for f in fichiers_a_supprimer if os.path.exists(f)) / (1024 ** 3)
        
        def action_supprimer(e):
            page.close(dialog_nettoyage)
            for f in fichiers_a_supprimer:
                if os.path.exists(f): os.remove(f)
            
            if os.path.exists(DB_NAME):
                conn = sqlite3.connect(DB_NAME)
                conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
                conn.commit()
                conn.close()
            
            for frame_info in inspect.stack():
                if "update_gallery" in frame_info.frame.f_locals:
                    frame_info.frame.f_locals["update_gallery"]()
                    break
            safe_show_snackbar(f"Nettoyage réussi !")

        dialog_nettoyage = ft.AlertDialog(
            title=ft.Text("🎬 Fin de visionnage"),
            content=ft.Text(f"Supprimer le film et ses sous-titres ? (Gain: {taille_go:.2f} Go)"),
            actions=[ft.TextButton("Conserver", on_click=lambda e: page.close(dialog_nettoyage)), 
                     ft.ElevatedButton("Supprimer", bgcolor="#E50914", color="white", on_click=action_supprimer)]
        )
        page.open(dialog_nettoyage)

    async def run_clean_async(video_path, video_id):
        proposer_nettoyage_hybride(video_path, video_id)

    # ==========================================
    # ÉLÉMENTS DE L'INTERFACE & APERÇU NETFLIX
    # ==========================================
    global poster_image, right_panel_video

    app_bg = ft.Container(
        image=None, 
        blur=ft.Blur(config.get("background_blur", 40), config.get("background_blur", 40), ft.BlurTileMode.CLAMP), 
        animate_opacity=500, 
        opacity=0.0, 
        expand=True
    )
    
    # Poster rendu visible par défaut pour réserver la taille de 280x420
    poster_image = ft.Image(
        src_base64=TRANSPARENT_PLACEHOLDER, 
        width=280, 
        height=420, 
        fit=ft.ImageFit.COVER, 
        border_radius=12, 
        visible=True
    )

    # Lecteur vidéo d'aperçu pour le Panneau de Droite (Option B)
    try:
        import flet_video
        right_panel_video = flet_video.Video(
            width=280,
            height=420,
            autoplay=True,
            muted=True,
            visible=False,
            aspect_ratio=16/9
        )
    except Exception:
        right_panel_video = ft.Container(
            width=280,
            height=420,
            visible=False,
            alignment=ft.alignment.center,
            content=ft.Text("Aperçu indisponible", color="white")
        )

    # Superposition du Poster et de la Vidéo d'Aperçu
    right_poster_box = ft.Container(
        content=ft.Stack([
            poster_image,
            right_panel_video
        ], width=280, height=420),
        width=280,
        height=420,
        border_radius=12,
        clip_behavior=ft.ClipBehavior.HARD_EDGE
    )

    title_text = ft.Text("Sélectionnez un film", size=28, weight="bold", color="white")
    rating_text = ft.Text("Note: N/A", size=15, color="#ffc107", weight="bold")
    cast_text = ft.Text("Casting: N/A", size=14, color="grey400")
    synopsis_text = ft.Text("Le résumé apparaîtra ici...", size=14, max_lines=12, overflow="ellipsis", color="white")

    player_options = [ft.dropdown.Option(k) for k in config["players"].keys() if config["players"][k]]
    player_dropdown = ft.Dropdown(label="Lecteur préféré", options=player_options, width=240, border_color="#E50914")
    if player_options: 
        player_dropdown.value = player_options[0].key

    def delete_custom_player(e):
        p_del = player_dropdown.value
        if p_del and p_del in config["players"]:
            del config["players"][p_del]
            save_config(config)
            player_dropdown.options = [opt for opt in player_dropdown.options if opt.key != p_del]
            page.update()
            show_snackbar(f"Lecteur '{p_del}' supprimé !")

    delete_player_button = ft.ElevatedButton(
        "Supprimer ce lecteur", 
        icon=ft.Icons.DELETE, 
        on_click=delete_custom_player, 
        style=ft.ButtonStyle(bgcolor={"": "#E50914"}, color={"": "white"})
    )
    language_dropdown = ft.Dropdown(
        label="Langue", 
        options=[ft.dropdown.Option(code, label) for code, label in SUBTITLE_LANGUAGES], 
        width=200, 
        value="fr", 
        border_color="#E50914"
    )

    # ==========================================
    # LOGIQUE PREVIEW & POSITIONS DE LECTURE
    # ==========================================
    hover_timer = None
    current_preview_movie_id = None
    PREVIEW_MODE = "A"
    HOVER_DELAY_SECONDS = 3.2
    POSITIONS_FILE = "omnicine_positions.json"

    def log_debug(msg: str):
        print(f"[OMNICINE-TRACER] {msg}", flush=True)

    def get_movie_unique_key(movie_data, file_case=None) -> str:
        if file_case and isinstance(file_case, str):
            return f"file_{os.path.normcase(os.path.abspath(file_case))}"
        if isinstance(movie_data, dict):
            loc = detect_file_location(movie_data)
            if loc and os.path.exists(loc):
                return f"file_{os.path.normcase(os.path.abspath(loc))}"
            return str(movie_data.get("id") or movie_data.get("title") or "default_media")
        return str(movie_data)

    def load_all_watch_positions() -> dict:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_watch_position(movie_key: str, seconds: int):
        if not movie_key or seconds <= 0:
            return
        positions = load_all_watch_positions()
        positions[str(movie_key)] = {
            "position": int(seconds),
            "updated_at": time.time()
        }
        try:
            with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(positions, f, indent=2, ensure_ascii=False)
            log_debug(f"Position sauvegardée dans JSON : {seconds}s (Clé: {movie_key})")
        except Exception as err:
            log_debug(f"Erreur écriture JSON position : {err}")

    def get_watch_position(movie_key: str) -> int:
        if not movie_key:
            return 0
        positions = load_all_watch_positions()
        item = positions.get(str(movie_key))
        if isinstance(item, dict):
            return item.get("position", 0)
        return 0

    def format_seconds_to_time(seconds: int) -> str:
        mins, secs = divmod(int(seconds), 60)
        hrs, mins = divmod(mins, 60)
        if hrs > 0:
            return f"{hrs}h {mins:02d}m {secs:02d}s"
        return f"{mins:02d}m {secs:02d}s"

    def remux_audio_track(file_path: str, audio_stream_idx: int) -> str:
        temp_dir = tempfile.gettempdir()
        out_path = os.path.join(temp_dir, "omnicine_remux_temp.mkv")
        cmd = [
            "ffmpeg", "-y", "-i", file_path,
            "-map", "0:v:0?", "-map", f"0:{audio_stream_idx}",
            "-c", "copy", out_path
        ]
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            log_debug(f"Remuxing audio FFmpeg vers index 0:{audio_stream_idx}...")
            subprocess.run(cmd, capture_output=True, startupinfo=startupinfo, timeout=10)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except Exception as err:
            log_debug(f"Erreur remuxing audio FFmpeg : {err}")
        return file_path

    def extract_subtitle_track(file_path: str, sub_stream_idx: int, codec_name: str = "") -> str | None:
        codec_lower = codec_name.lower()
        if "pgs" in codec_lower or "dvd" in codec_lower or "hdmv" in codec_lower:
            log_debug(f"Piste 0:{sub_stream_idx} ignorée (format image {codec_name}).")
            return None

        temp_dir = tempfile.gettempdir()
        out_path = os.path.join(temp_dir, "omnicine_sub_temp.vtt")
        cmd = [
            "ffmpeg", "-y",
            "-vn", "-an",
            "-i", file_path,
            "-map", f"0:{sub_stream_idx}",
            "-c:s", "webvtt",
            out_path
        ]
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            log_debug(f"Extraction sous-titres (index 0:{sub_stream_idx})...")
            subprocess.run(cmd, capture_output=True, startupinfo=startupinfo, timeout=4)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except Exception as err:
            log_debug(f"Extraction sous-titres annulée/échouée : {err}")
        return None

    def extract_real_media_tracks(file_path: str):
        audio_options = []
        sub_options = [ft.dropdown.Option(key="none", text="Désactivés")]

        if not file_path or not os.path.exists(file_path):
            return [ft.dropdown.Option(key="default", text="Piste audio par défaut")], sub_options

        try:
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path]
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo, timeout=4)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                
                a_idx = 1
                s_idx = 1
                for stream in streams:
                    st_type = stream.get("codec_type")
                    s_global_idx = stream.get("index")
                    codec = str(stream.get("codec_name", "")).upper()
                    tags = stream.get("tags", {}) or {}
                    lang = str(tags.get("language", "UND")).upper()
                    title = tags.get("title", "")
                    
                    if st_type == "audio":
                        channels = stream.get("channels", "")
                        ch_label = f"{channels}ch" if channels else ""
                        label = f"Audio #{a_idx}: [{lang}] {codec} {ch_label}".strip()
                        if title: label += f" ({title})"
                        audio_options.append(ft.dropdown.Option(key=str(s_global_idx), text=label))
                        a_idx += 1

                    elif st_type == "subtitle":
                        is_image = "PGS" in codec or "DVD" in codec or "HDMV" in codec
                        tag = "[Image - Incompatible]" if is_image else f"[{codec}]"
                        label = f"Sub #{s_idx}: [{lang}] {tag}".strip()
                        if title: label += f" ({title})"
                        sub_options.append(ft.dropdown.Option(key=f"{s_global_idx}:{codec}", text=label))
                        s_idx += 1

                if audio_options:
                    return audio_options, sub_options
        except Exception as err:
            log_debug(f"Erreur FFprobe : {err}")

        return [ft.dropdown.Option(key="default", text="Piste principale")], sub_options

    def get_video_control_classes():
        VideoClass, VideoMediaClass, SubtitleTrackClass = None, None, None
        try:
            import flet_video
            VideoClass = getattr(flet_video, "Video", None)
            VideoMediaClass = getattr(flet_video, "VideoMedia", None)
            SubtitleTrackClass = getattr(flet_video, "VideoSubtitleTrack", None)
        except Exception:
            pass

        if not VideoClass:
            try:
                import flet.video as ft_vid
                VideoClass = getattr(ft_vid, "Video", None)
                VideoMediaClass = getattr(ft_vid, "VideoMedia", None)
                SubtitleTrackClass = getattr(ft_vid, "VideoSubtitleTrack", None)
            except Exception:
                pass

        if not VideoClass:
            try:
                VideoClass = getattr(ft, "Video", None)
                VideoMediaClass = getattr(ft, "VideoMedia", None)
                SubtitleTrackClass = getattr(ft, "VideoSubtitleTrack", None)
            except Exception:
                pass

        return VideoClass, VideoMediaClass, SubtitleTrackClass

    def format_path_for_player(path_or_url: str) -> str:
        if not path_or_url:
            return ""
        if path_or_url.startswith("http://") or path_or_url.startswith("https://") or path_or_url.startswith("file://"):
            return path_or_url
        try:
            clean_path = path_or_url.replace("\\", "/")
            if not clean_path.startswith("/"):
                clean_path = "/" + clean_path
            return f"file://{urllib.parse.quote(clean_path, safe='/:')}"
        except Exception:
            clean_path = path_or_url.replace("\\", "/")
            if not clean_path.startswith("/"):
                clean_path = "/" + clean_path
            return f"file://{clean_path}"

    def detect_file_location(movie_data: dict | str) -> str | None:
        if isinstance(movie_data, str):
            return movie_data if os.path.exists(movie_data) else None

        if not isinstance(movie_data, dict):
            return None

        possible_keys = [
            "local_path", "file_path", "network_path", "nas_path",
            "path", "filepath", "file", "video_path", "src", "location"
        ]
        for key in possible_keys:
            val = movie_data.get(key)
            if val and isinstance(val, str) and os.path.exists(val):
                return val

        movie_id = str(movie_data.get("id", ""))
        title = str(movie_data.get("title", ""))
        clean_title = "".join(c for c in title if c.isalnum()).lower().strip()

        storage_dirs = ["cache_trailers", "downloads", "movies", "media", r"\\127.0.0.1\movies"]
        for s_dir in storage_dirs:
            if os.path.exists(s_dir):
                try:
                    for file_name in os.listdir(s_dir):
                        fn_lower = file_name.lower()
                        if fn_lower.endswith((".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v")):
                            if (movie_id and movie_id != "None" and movie_id in fn_lower) or (clean_title and clean_title in fn_lower):
                                return os.path.join(s_dir, file_name)
                except Exception:
                    continue

        return movie_data.get("trailer_url") or movie_data.get("video_url") or movie_data.get("url")

    def execute_movie_launch(movie_data, start_position: int = 0):
        if not movie_data:
            return

        file_path = detect_file_location(movie_data)
        log_debug(f"Lancement du lecteur externe. Fichier cible : {file_path}")

        if not file_path or not os.path.exists(file_path):
            return

        key = get_movie_unique_key(movie_data, file_path)
        if start_position > 0:
            save_watch_position(key, start_position)

        combined_scope = {}
        frame = inspect.currentframe()
        while frame:
            combined_scope.update(frame.f_locals)
            frame = frame.f_back
        combined_scope.update(globals())

        candidate_names = [
            "launch_external_player", "play_movie_in_main_player", "play_movie", 
            "play_video", "launch_movie", "open_movie", "lancer_film", "lire_film"
        ]

        launcher_func = None
        for name in candidate_names:
            func = combined_scope.get(name)
            if func and callable(func):
                launcher_func = func
                break

        if launcher_func:
            try:
                launcher_func(file_path)
                return
            except Exception:
                pass
            try:
                launcher_func(movie_data)
                return
            except Exception:
                pass

        try:
            os.startfile(file_path)
        except Exception as err_os:
            log_debug(f"Erreur lancement système : {err_os}")

    # =========================================================================
    # OVERLAY PLAYBACK TRACKER & LECTEUR
    # =========================================================================
    preview_video_player_container = ft.Container(expand=True)
    preview_title_text = ft.Text("", size=22, weight="bold", color="white")
    preview_subtitle_text = ft.Text("", size=14, color="grey400")
    preview_resume_info_text = ft.Text("", size=13, color="#E50914", weight="bold")
    
    preview_active_movie = [None]
    current_remixed_path = [None]
    current_live_position = [0]
    playback_tracker_active = [False]

    dropdown_audio_track = ft.Dropdown(label="🔊 Piste Audio", width=300, options=[], dense=True)
    dropdown_subtitle_track = ft.Dropdown(label="💬 Sous-titres", width=250, options=[], dense=True)

    def start_position_tracker(movie_key: str, initial_pos: int):
        playback_tracker_active[0] = True
        start_time = time.time() - initial_pos

        def tracker_loop():
            while playback_tracker_active[0]:
                time.sleep(1.0)
                if not playback_tracker_active[0]:
                    break
                elapsed = int(time.time() - start_time)
                if elapsed > 0:
                    current_live_position[0] = elapsed
                    save_watch_position(movie_key, elapsed)

        threading.Thread(target=tracker_loop, daemon=True).start()

    def stop_position_tracker():
        playback_tracker_active[0] = False

    def build_overlay_player(file_path_to_play: str, start_sec: int = 0, sub_path: str = None, movie_key: str = ""):
        stop_position_tracker()
        VideoClass, VideoMediaClass, SubtitleTrackClass = get_video_control_classes()
        
        if not VideoClass or not VideoMediaClass:
            preview_video_player_container.content = ft.Container(
                content=ft.Text("Lecteur vidéo indisponible.", color="white"),
                alignment=ft.alignment.center
            )
            return

        player_url = format_path_for_player(file_path_to_play)

        try:
            sig = inspect.signature(VideoClass.__init__)
            supported_params = sig.parameters.keys()
        except Exception:
            supported_params = []

        video_kwargs = {
            "playlist": [VideoMediaClass(player_url)],
            "autoplay": True,
            "muted": False,
            "show_controls": True,
            "aspect_ratio": 16/9,
            "expand": True
        }

        if sub_path and SubtitleTrackClass and "subtitle_tracks" in supported_params:
            sub_url = format_path_for_player(sub_path)
            video_kwargs["subtitle_tracks"] = [SubtitleTrackClass(src=sub_url, label="Sous-titres")]

        try:
            mini_player = VideoClass(**video_kwargs)
        except TypeError:
            video_kwargs.pop("subtitle_tracks", None)
            mini_player = VideoClass(**video_kwargs)

        preview_video_player_container.content = mini_player
        page.update()

        if movie_key:
            start_position_tracker(movie_key, start_sec)

        if start_sec > 0:
            def apply_seek():
                time.sleep(1.2)
                try:
                    if hasattr(mini_player, "seek"):
                        try:
                            mini_player.seek(int(start_sec * 1000))
                        except Exception:
                            mini_player.seek(int(start_sec))
                    elif hasattr(mini_player, "seek_to"):
                        mini_player.seek_to(int(start_sec * 1000))
                    page.update()
                    log_debug(f"Seek position appliqué : {start_sec}s")
                except Exception as err_seek:
                    log_debug(f"Seek échoué : {err_seek}")

            threading.Thread(target=apply_seek, daemon=True).start()

    def on_audio_track_selected(e):
        if not preview_active_movie[0]:
            return
        
        raw_path = detect_file_location(preview_active_movie[0])
        m_key = get_movie_unique_key(preview_active_movie[0], raw_path)
        sel_key = dropdown_audio_track.value
        
        # 1. On effectue le traitement lourd en premier (sans bloquer ou figer la variable de temps)
        remixed = raw_path
        if sel_key and sel_key.isdigit() and raw_path:
            remixed = remux_audio_track(raw_path, int(sel_key))
            
        sub_key_raw = dropdown_subtitle_track.value
        sub_file = None
        if sub_key_raw and ":" in sub_key_raw and raw_path:
            s_idx, s_codec = sub_key_raw.split(":", 1)
            sub_file = extract_subtitle_track(raw_path, int(s_idx), s_codec)

        # 2. ANCRAGE DE SÉCURITÉ : Si FFmpeg a échoué (renvoie le même fichier original) ou a expiré,
        # on ne touche absolument pas au lecteur existant. Il continue à tourner sans aucune coupure.
        if remixed == raw_path and (sub_file is None or dropdown_subtitle_track.value == "none"):
            if current_remixed_path[0] is None or current_remixed_path[0] == raw_path:
                log_debug("Échec ou absence de traitement audio/sous-titre (Timeout FFmpeg). Lecture préservée en continu.")
                return

        current_remixed_path[0] = remixed
        
        # 3. Capture ultra-fraîche de la position immédiate APRÈS les secondes perdues par le timeout
        exact_pos = current_live_position[0] if current_live_position[0] > 0 else get_watch_position(m_key)
        build_overlay_player(remixed, start_sec=exact_pos, sub_path=sub_file, movie_key=m_key)

    def on_subtitle_track_selected(e):
        if not preview_active_movie[0]:
            return
        
        raw_path = detect_file_location(preview_active_movie[0])
        m_key = get_movie_unique_key(preview_active_movie[0], raw_path)
        media_src = current_remixed_path[0] or raw_path
        sel_key_raw = dropdown_subtitle_track.value
        
        # 1. Traitement de la piste sans bloquer le repère temporel en amont
        sub_vtt = None
        if sel_key_raw and ":" in sel_key_raw and raw_path:
            s_idx, s_codec = sel_key_raw.split(":", 1)
            sub_vtt = extract_subtitle_track(raw_path, int(s_idx), s_codec)

        # 2. ANCRAGE DE SÉCURITÉ : Si l'extraction de sous-titre plante ou subit un timeout,
        # on ignore la demande de reconstruction pour laisser la vidéo continuer paisiblement sa route.
        if sub_vtt is None and sel_key_raw != "none":
            log_debug("Échec d'extraction des sous-titres (Timeout). Annulation du rechargement pour éviter la coupure.")
            return

        # 3. Évaluation dynamique du repère au millième de seconde près avant ré-initialisation
        exact_pos = current_live_position[0] if current_live_position[0] > 0 else get_watch_position(m_key)
        build_overlay_player(media_src, start_sec=exact_pos, sub_path=sub_vtt, movie_key=m_key)

    dropdown_audio_track.on_change = on_audio_track_selected
    dropdown_subtitle_track.on_change = on_subtitle_track_selected

    def close_preview_modal(e=None):
        stop_hover_preview()

    def launch_overlay_resume(e=None):
        if preview_active_movie[0]:
            target_movie = preview_active_movie[0]
            raw_p = detect_file_location(target_movie)
            key = get_movie_unique_key(target_movie, raw_p)
            saved_pos = get_watch_position(key)
            trigger_preview(target_movie, force_start_pos=saved_pos)

    def launch_overlay_restart(e=None):
        if preview_active_movie[0]:
            target_movie = preview_active_movie[0]
            raw_p = detect_file_location(target_movie)
            key = get_movie_unique_key(target_movie, raw_p)
            save_watch_position(key, 0)
            current_live_position[0] = 0
            trigger_preview(target_movie, force_start_pos=0)

    def launch_external_player_click(e=None):
        if preview_active_movie[0]:
            target_movie = preview_active_movie[0]
            raw_p = detect_file_location(target_movie)
            key = get_movie_unique_key(target_movie, raw_p)
            saved_pos = current_live_position[0] or get_watch_position(key)
            stop_hover_preview()
            execute_movie_launch(target_movie, start_position=saved_pos)

    def delete_file_action(e=None):
        if not preview_active_movie[0]:
            return

        target_movie = preview_active_movie[0]
        file_path = detect_file_location(target_movie)

        if not file_path or not os.path.exists(file_path):
            return

        def confirm_delete(e_confirm):
            try:
                os.remove(file_path)
                dlg_modal.open = False
                stop_hover_preview()
            except Exception as err_del:
                log_debug(f"Erreur suppression : {err_del}")

        def cancel_delete(e_cancel):
            dlg_modal.open = False
            page.update()

        dlg_modal = ft.AlertDialog(
            modal=True,
            title=ft.Text("🗑️ Confirmation de suppression"),
            content=ft.Text(f"Voulez-vous supprimer ce fichier ?\n\n{file_path}"),
            actions=[
                ft.TextButton("Annuler", on_click=cancel_delete),
                ft.ElevatedButton("Supprimer", bgcolor="red", color="white", on_click=confirm_delete),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        page.dialog = dlg_modal
        dlg_modal.open = True
        page.update()

    btn_play_overlay_resume = ft.ElevatedButton("▶ Reprendre", style=ft.ButtonStyle(bgcolor="#E50914", color="white"), on_click=launch_overlay_resume)
    btn_play_overlay_restart = ft.ElevatedButton("↺ Recommencer", style=ft.ButtonStyle(bgcolor="#333333", color="white"), on_click=launch_overlay_restart)
    btn_play_external = ft.ElevatedButton("📺 Lecteur Externe (VLC / MPC)", style=ft.ButtonStyle(bgcolor="#222222", color="white"), on_click=launch_external_player_click)
    btn_delete_file = ft.OutlinedButton("🗑️ Supprimer le fichier", style=ft.ButtonStyle(color="#FF4D4D", side=ft.BorderSide(1, "#FF4D4D")), on_click=delete_file_action)
    btn_close_preview = ft.IconButton(icon=ft.Icons.CLOSE, icon_color="white", on_click=close_preview_modal)

    preview_modal_card = ft.Container(
        width=950,
        bgcolor="#181818",
        border_radius=12,
        padding=20,
        left=100,
        top=50,
        shadow=ft.BoxShadow(spread_radius=4, blur_radius=25, color="black")
    )

    def on_overlay_drag(e: ft.DragUpdateEvent):
        preview_modal_card.left = max(0, (preview_modal_card.left or 100) + e.delta_x)
        preview_modal_card.top = max(0, (preview_modal_card.top or 50) + e.delta_y)
        preview_modal_card.update()

    draggable_header = ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.MOVE,
        on_pan_update=on_overlay_drag,
        content=ft.Container(
            content=ft.Row([
                ft.Column([
                    preview_title_text,
                    preview_subtitle_text,
                ]),
                btn_close_preview
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.padding.only(bottom=5)
        )
    )

    preview_modal_card.content = ft.Column([
        draggable_header,
        ft.Container(height=5),
        ft.Container(
            content=preview_video_player_container,
            height=430,
            border_radius=8,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            bgcolor="black"
        ),
        ft.Container(height=10),
        ft.Row([
            dropdown_audio_track,
            dropdown_subtitle_track,
            preview_resume_info_text
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Container(height=10),
        ft.Row([
            ft.Row([btn_play_overlay_resume, btn_play_overlay_restart, btn_play_external]),
            btn_delete_file
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
    ], tight=True)

    preview_overlay_container = ft.Container(
        content=ft.Stack([
            ft.Container(expand=True, bgcolor="#CC000000"),
            preview_modal_card
        ]),
        expand=True,
        visible=False
    )

    def stop_hover_preview(card_video_container=None):
        nonlocal hover_timer, current_preview_movie_id
        
        stop_position_tracker()

        if hover_timer is not None:
            try:
                hover_timer.cancel()
            except Exception:
                pass
            hover_timer = None

        if preview_active_movie[0] and current_live_position[0] > 0:
            file_loc = detect_file_location(preview_active_movie[0])
            key = get_movie_unique_key(preview_active_movie[0], file_loc)
            save_watch_position(key, current_live_position[0])
            
        current_preview_movie_id = None
        preview_active_movie[0] = None
        current_remixed_path[0] = None
        current_live_position[0] = 0

        try:
            preview_overlay_container.visible = False
            preview_video_player_container.content = None

            if 'right_panel_video' in globals() and right_panel_video:
                try:
                    right_panel_video.playlist = []
                    right_panel_video.visible = False
                except Exception:
                    pass
            if 'poster_image' in globals():
                poster_image.visible = True

            if card_video_container:
                card_video_container.content = None
                card_video_container.visible = False

            page.update()
            log_debug("Lecteur overlay fermé.")
        except Exception as err:
            log_debug(f"Erreur fermeture overlay : {err}")

    def trigger_preview(movie_data, card_video_container=None, force_start_pos=None):
        title = movie_data.get('title', 'Inconnu') if isinstance(movie_data, dict) else 'Inconnu'
        log_debug(f"CHARGEMENT OVERLAY DU FILM : {title}")
        
        try:
            raw_path = detect_file_location(movie_data)
            if not raw_path:
                return

            player_url = format_path_for_player(raw_path)
            movie_key = get_movie_unique_key(movie_data, raw_path)

            audio_opts, sub_opts = extract_real_media_tracks(raw_path)
            dropdown_audio_track.options = audio_opts
            dropdown_audio_track.value = audio_opts[0].key if audio_opts else None
            
            dropdown_subtitle_track.options = sub_opts
            dropdown_subtitle_track.value = sub_opts[0].key if sub_opts else None

            saved_position = force_start_pos if force_start_pos is not None else get_watch_position(movie_key)
            current_live_position[0] = saved_position

            if saved_position > 5:
                preview_resume_info_text.value = f"📌 Reprise : {format_seconds_to_time(saved_position)}"
                btn_play_overlay_resume.visible = True
            else:
                preview_resume_info_text.value = "▶ Lecture au début"
                btn_play_overlay_resume.visible = False

            if PREVIEW_MODE == "A":
                preview_active_movie[0] = movie_data
                preview_title_text.value = title
                preview_subtitle_text.value = f"Genre: {movie_data.get('genre', 'Film')}" if isinstance(movie_data, dict) else ""

                log_debug(f"Initialisation du lecteur à {saved_position}s (Clé: {movie_key})...")
                build_overlay_player(raw_path, start_sec=saved_position, sub_path=None, movie_key=movie_key)

                preview_overlay_container.visible = True

                if preview_overlay_container not in page.overlay:
                    page.overlay.append(preview_overlay_container)

                page.update()

        except Exception as err:
            import traceback
            log_debug(f"ERREUR TRIGGER_PREVIEW : {err}\n{traceback.format_exc()}")

    def handle_movie_hover(e, movie_data, card_video_container=None):
        nonlocal hover_timer, current_preview_movie_id

        is_hovered = str(e.data).lower() in ("true", "1")
        movie_id = movie_data.get("id") if isinstance(movie_data, dict) else None

        if is_hovered:
            if current_preview_movie_id == movie_id and hover_timer is not None:
                return

            if hover_timer is not None:
                try:
                    hover_timer.cancel()
                except Exception:
                    pass
                hover_timer = None

            current_preview_movie_id = movie_id
            hover_timer = threading.Timer(HOVER_DELAY_SECONDS, lambda: trigger_preview(movie_data, card_video_container))
            hover_timer.start()

        else:
            if hover_timer is not None and not preview_overlay_container.visible:
                try:
                    hover_timer.cancel()
                except Exception:
                    pass
                hover_timer = None
                current_preview_movie_id = None

    def create_movie_card(movie):
        card_video_container = ft.Container(visible=False, expand=True)

        def on_card_click(e):
            stop_hover_preview(card_video_container)
            file_p = detect_file_location(movie)
            key = get_movie_unique_key(movie, file_p)
            saved_pos = get_watch_position(key)
            execute_movie_launch(movie, start_position=saved_pos)

        card = ft.Container(
            content=ft.Stack([
                ft.Image(
                    src=movie.get("poster_url", TRANSPARENT_PLACEHOLDER) if isinstance(movie, dict) else TRANSPARENT_PLACEHOLDER,
                    width=180,
                    height=270,
                    fit=ft.ImageFit.COVER,
                    border_radius=8
                ),
                card_video_container
            ]),
            on_hover=lambda e: handle_movie_hover(e, movie, card_video_container),
            on_click=on_card_click,
            border_radius=8
        )
        return card

    # ==========================================
    # BLOC PANNEAU DROIT (AFFICHAGE POSTER + VIDÉO)
    # ==========================================
    right_poster_box = ft.Container(
        content=ft.Stack([
            poster_image,        # L'image statique
            right_panel_video    # Le lecteur vidéo superposé
        ]),
        width=280,
        height=420,
        border_radius=12,
        clip_behavior=ft.ClipBehavior.HARD_EDGE
    )

    # ==========================================
    # LECTEUR VIDÉO NATIF
    # ==========================================
    if FLET_VIDEO_AVAILABLE:
        native_video_player = flet_video.Video(expand=True, visible=False, aspect_ratio=16/9, autoplay=True, fill_color=ft.Colors.BLACK)
    else:
        native_video_player = ft.Container(expand=True, visible=False, alignment=ft.alignment.center, content=ft.Text("Lecteur indisponible", color="white"))

    def close_native_player(e):
        current_src = native_video_player.src if FLET_VIDEO_AVAILABLE else None
        native_video_player.visible = False
        if FLET_VIDEO_AVAILABLE: native_video_player.src = None
        page.update()
        if current_src and not current_src.startswith("http") and os.path.exists(current_src):
            video_id = next((v[0] for v in videos if v[3] == current_src), None)
            if video_id: page.run_task(run_clean_async, current_src, video_id)

    video_player_dialog = ft.AlertDialog(
        title=ft.Text("Lecture", size=20, weight="bold", color="white"), bgcolor="#000000",
        content=ft.Container(content=native_video_player, width=800, height=450),
        actions=[ft.ElevatedButton("Fermer", on_click=close_native_player)]
    )

    # ==========================================
    # DIALOGUE DE BANDES-ANNONCES (MIGRATION FLET-VIDEO)
    # ==========================================
    if FLET_VIDEO_AVAILABLE:
        trailer_video_player = flet_video.Video(
            expand=True,
            visible=False,
            aspect_ratio=16/9,
            autoplay=True,
            volume=1.0,
            muted=False,
            fill_color=ft.Colors.BLACK
        )
    else:
        # Fallback si flet-video n'est pas disponible
        trailer_video_player = ft.Container(
            content=ft.Text("Lecteur vidéo non disponible", color="grey400"),
            visible=False,
            height=220,
            alignment=ft.alignment.center,
            bgcolor=ft.Colors.with_opacity(0.3, ft.Colors.BLACK)
        )
        
    def close_trailer_player(e):
        """Ferme le lecteur de bande-annonce."""
        trailer_video_player.visible = False
        if FLET_VIDEO_AVAILABLE and hasattr(trailer_video_player, 'src'):
            trailer_video_player.src = None
        page.close(trailer_dialog)
        
    trailer_dialog = ft.AlertDialog(
        title=ft.Text("Bande-Annonce", size=20, weight="bold", color="white"),
        bgcolor="#000000",
        content=ft.Container(
            content=trailer_video_player,
            width=800,
            height=450
        ),
        actions=[
            ft.ElevatedButton(
                "Fermer",
                on_click=close_trailer_player,
                style=ft.ButtonStyle(bgcolor=ft.Colors.TRANSPARENT, color="white")
            )
        ]
    )
        
    def play_trailer(e):
        """
        Lecteur OmniCine HD - Streaming Adaptatif Réel (type Netflix/Prime).

        Principes : 
        1. Sélection préalable de la langue (VF/VO/VOSTFR), de la qualité et destination sans rupture d'immersion.
        2. Extraction prioritaire haute fidélité (Apple Trailers ou YouTube HD natif).
        3. Récupération et téléchargement physique du flux de haute qualité pour contourner les blocages de YouTube.
        4. Lecture intégrée au cœur de l'application (Flet 0.27.6) via flet-video et le serveur local FastAPI.
        5. Boîte de dialogue de nettoyage automatique post-visionnage pour préserver le stockage.
        """
        # CORRECTIF CRITIQUE : Suppression du nonlocal qui provoquait le SyntaxError.
        # Python accède nativement aux variables des scopes parents/globaux en lecture seule.

        import os
        import re
        import threading
        import urllib.request
        import flet as ft
        import yt_dlp
        import webbrowser

        page = e.page

        # 1. Extraction ID et Titre Initiale
        video_id = None
        if current_trailers and isinstance(current_trailers, list):
            video_id = current_trailers[0].get("key")

        search_title = ""
        if current_selected_video:
            if isinstance(current_selected_video, tuple):
                search_title = current_selected_video[1]
            else:
                search_title = current_selected_video.get("title", "")

        if not video_id and not search_title:
            return

        def _url_reachable(url, timeout=4):
            """Vérifie rapidement qu'une URL répond avant de la donner au lecteur."""
            try:
                req = urllib.request.Request(url, method="HEAD")
                req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return 200 <= resp.status < 400
            except Exception:
                return False

        def prompt_delete(filename):
            """Demande poliment à l'utilisateur s'il souhaite purger le fichier du disque dur."""
            def confirm_delete(evt):
                try:
                    filepath = os.path.join("cache_trailers", filename)
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    page.close(delete_dialog)
                    page.open(ft.SnackBar(ft.Text("Espace disque libéré : Bande-annonce supprimée."), bgcolor=ft.Colors.GREEN_800))
                except Exception as ex_del:
                    page.open(ft.SnackBar(ft.Text(f"Erreur lors du nettoyage : {ex_del}"), bgcolor=ft.Colors.RED_800))

            def cancel_delete(evt):
                page.close(delete_dialog)

            delete_dialog = ft.AlertDialog(
                title=ft.Text("Optimisation du stockage", weight=ft.FontWeight.BOLD),
                content=ft.Text("Voulez-vous supprimer la bande-annonce téléchargée pour éviter de saturer votre disque dur ?"),
                actions=[
                    ft.TextButton("Oui, supprimer", on_click=confirm_delete, style=ft.ButtonStyle(color=ft.Colors.RED_ACCENT)),
                    ft.TextButton("Non, conserver", on_click=cancel_delete, style=ft.ButtonStyle(color=ft.Colors.WHITE)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                modal=True,
            )
            page.open(delete_dialog)

        # 3. Worker Thread (Arrière-plan propre optimisé en mode hybride)
        def worker(lang_choice, quality_choice, target_destination):
            try:
                # Déclenchement visuel de l'UI de chargement d'origine
                page.open(loading_dialog)
                e.control.text = "Préparation..."
                e.control.update()

                lang_suffix = "bande annonce vf" if lang_choice == "VF" else "vostfr trailer" if lang_choice == "VOSTFR" else "official trailer"
                use_search = True if (lang_choice in ["VF", "VOSTFR"] and not video_id) else False
                
                # Optimisation : On génère directement la requête finale pour éviter la double recherche
                if video_id and not use_search:
                    query = f"https://www.youtube.com/watch?v={video_id}"
                    clean_id = video_id
                else:
                    query = f"ytsearch1:{search_title} {lang_suffix}"
                    clean_id = "".join(c for c in search_title if c.isalnum())

                filename_base = f"trailer_{clean_id}_{lang_choice}_{quality_choice}"
                outtmpl_path = os.path.join("cache_trailers", f"{filename_base}.%(ext)s")

                # Initialisation des variables de contrôle du système hybride
                downloaded_filename = None
                is_local = False
                play_url = None

                # --- VÉRIFICATION DU CACHE PHYSIQUE EXISTANT ---
                if os.path.exists("cache_trailers"):
                    for f in os.listdir("cache_trailers"):
                        if f.startswith(filename_base):
                            downloaded_filename = f
                            is_local = True
                            play_url = f"http://127.0.0.1:8080/stream/trailer/{downloaded_filename}"
                            break

                # --- SI PAS EN CACHE, ON FORCE LA PRIORITÉ AU STREAMING DIRECT ---
                if not play_url:
                    loading_dialog.title.value = "Négociation du flux HD..."
                    loading_dialog.content.content.controls[1].value = "Extraction du lien de streaming instantané..."
                    loading_dialog.update()

                    ydl_opts_stream = {
                        "quiet": True,
                        "extract_flat": False,
                        "format": f"bestvideo[height<={quality_choice}]+bestaudio/best[height<={quality_choice}]",
                        "format_sort": ["acodec", "channels:6"],
                    }

                    try:
                        with yt_dlp.YoutubeDL(ydl_opts_stream) as ydl:
                            res = ydl.extract_info(query, download=False)
                            if res and "entries" in res and res["entries"]:
                                video_info = res["entries"][0]
                            else:
                                video_info = res
                            
                            play_url = video_info.get("url")
                            
                            if play_url:
                                is_local = False
                            else:
                                raise Exception("Lien direct introuvable.")

                    except Exception:
                        # --- CEINTURE DE SÉCURITÉ : LE MODE STREAMING A ÉCHOUÉ, BASCULE SUR LE TÉLÉCHARGEMENT ---
                        loading_dialog.title.value = "Mode secours activé..."
                        loading_dialog.content.content.controls[1].value = "Streaming direct bloqué. Initialisation du téléchargement physique..."
                        loading_dialog.update()

                        ydl_opts_download = {
                            "quiet": False,
                            "no_warnings": False,
                            "format": f"bestvideo[height<={quality_choice}]+bestaudio/best[height<={quality_choice}]",
                            "outtmpl": outtmpl_path,
                            "merge_output_format": "mp4",
                            "format_sort": ["acodec", "channels:6"], 
                        }

                        with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
                            ydl.extract_info(query, download=True)

                        if os.path.exists("cache_trailers"):
                            for f in os.listdir("cache_trailers"):
                                if f.startswith(filename_base):
                                    downloaded_filename = f
                                    is_local = True
                                    break

                        if not downloaded_filename:
                            raise Exception("Le flux vidéo n'a pas pu être matérialisé localement.")

                        play_url = f"http://127.0.0.1:8080/stream/trailer/{downloaded_filename}"
                        is_local = True

                # Fermeture de l'indicateur de chargement
                try:
                    page.close(loading_dialog)
                except Exception:
                    pass

                # 4. Routage intelligent selon le choix de l'usager
                if target_destination == "INTERNAL":
                    try:
                        import flet_video as ft_vid
                    except ImportError:
                        ft_vid = None

                    if ft_vid:
                        video_player = ft_vid.Video(
                            expand=True,
                            playlist=[ft_vid.VideoMedia(play_url)],
                            playlist_mode=ft_vid.PlaylistMode.NONE if hasattr(ft_vid, 'PlaylistMode') else "none",
                            aspect_ratio=16 / 9,
                            autoplay=True,
                        )
                    else:
                        video_player = ft.Video(
                            expand=True,
                            playlist=[ft.VideoMedia(play_url)],
                            playlist_mode=ft.PlaylistMode.NONE,
                            aspect_ratio=16 / 9,
                            autoplay=True,
                        )

                    def close_trailer(evt):
                        video_player.pause()
                        page.close(trailer_dialog)
                        # Déclenchement du nettoyage uniquement si le fichier a été physiquement écrit ou chargé localement
                        if is_local and downloaded_filename:
                            prompt_delete(downloaded_filename)

                    trailer_dialog = ft.AlertDialog(
                        content=ft.Container(
                            content=video_player,
                            width=1100,
                            height=620,
                            bgcolor=ft.Colors.BLACK,
                            border_radius=16,
                        ),
                        actions=[
                            ft.TextButton(
                                "Fermer",
                                on_click=close_trailer,
                                style=ft.ButtonStyle(color=ft.Colors.WHITE),
                            )
                        ],
                        actions_alignment=ft.MainAxisAlignment.END,
                        modal=True,
                    )
                    page.open(trailer_dialog)

                elif target_destination == "CAST":
                    # Double sécurité de détection d'IP pour le mode partage de connexion
                    import socket
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.connect(("8.8.8.8", 80))
                        local_ip = s.getsockname()[0]
                        s.close()
                    except Exception:
                        try:
                            # Méthode locale hors-ligne si pas d'accès WAN
                            local_ip = socket.gethostbyname(socket.gethostname())
                        except Exception:
                            local_ip = "127.0.0.1"

                    # Formatage correct de l'adresse réseau
                    network_video_url = play_url.replace("127.0.0.1", local_ip).replace("localhost", local_ip) if is_local else play_url

                    # Génération dynamique du QR Code (Base64) pour le S9 et autres mobiles (iOS/Android)
                    import io
                    import base64
                    try:
                        import qrcode
                        qr = qrcode.QRCode(version=1, box_size=10, border=4)
                        qr.add_data(network_video_url)
                        qr.make(fit=True)
                        img = qr.make_image(fill_color="black", back_color="white")
                        
                        buffered = io.BytesIO()
                        img.save(buffered, format="PNG")
                        qr_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                        qr_widget = ft.Image(src_base64=qr_base64, width=180, height=180, fit=ft.ImageFit.CONTAIN)
                    except Exception as qr_err:
                        qr_widget = ft.Column([
                            ft.Icon(ft.Icons.QR_CODE_SCANNER, size=80, color=ft.Colors.RED_400),
                            ft.Text(f"Erreur QR : {qr_err}", size=11, color=ft.Colors.RED_400)
                        ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

                    # Liste réceptrice Cast
                    device_list_column = ft.Column(
                        controls=[],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                        scroll=ft.ScrollMode.AUTO
                    )

                    # Scanner asynchrone non-bloquant pour Flet
                    def scan_cast_devices():
                        try:
                            import pychromecast
                            chromecasts, browser = pychromecast.get_chromecasts(timeout=4)
                            pychromecast.discovery.stop_discovery(browser)
                            
                            device_list_column.controls.clear()
                            
                            if not chromecasts:
                                device_list_column.controls.append(
                                    ft.Text("Aucun appareil Cast détecté.", size=12, color=ft.Colors.GREY_500, italic=True)
                                )
                                device_list_column.controls.append(
                                    ft.IconButton(
                                        icon=ft.Icons.REFRESH,
                                        icon_color=ft.Colors.RED_ACCENT,
                                        tooltip="Relancer la recherche",
                                        on_click=lambda _: start_scan()
                                    )
                                )
                            else:
                                device_list_column.controls.append(
                                    ft.Text(f"{len(chromecasts)} appareil(s) trouvé(s) :", size=12, color=ft.Colors.GREY_400)
                                )
                                for cast in chromecasts:
                                    def trigger_cast(e, target_cast=cast):
                                        e.control.disabled = True
                                        e.control.text = "Connexion..."
                                        page.update()
                                        try:
                                            target_cast.wait(timeout=5)
                                            
                                            # CORRECTIF CRITIQUE MULTI-ÉCOSYSTEME MULTIMÉDIA :
                                            if not is_local and video_id:
                                                from pychromecast.apps.youtube import YouTubeController
                                                yt_ctrl = YouTubeController()
                                                target_cast.register_handler(yt_ctrl)
                                                yt_ctrl.play_video(video_id)
                                                page.open(ft.SnackBar(ft.Text(f"Application YouTube lancée sur : {target_cast.name}"), bgcolor=ft.Colors.GREEN_800))
                                            else:
                                                media_ctrl = target_cast.media_controller
                                                media_ctrl.play_media(network_video_url, "video/mp4", title=f"OmniCine - {search_title}")
                                                media_ctrl.block_until_active(timeout=5)
                                                page.open(ft.SnackBar(ft.Text(f"Lecture lancée sur : {target_cast.name}"), bgcolor=ft.Colors.GREEN_800))
                                            
                                            page.close(cast_dialog)
                                        except Exception as play_err:
                                            page.open(ft.SnackBar(ft.Text(f"Erreur de lecture / Isolation Réseau : {play_err}"), bgcolor=ft.Colors.RED_800))
                                            e.control.disabled = False
                                            e.control.text = target_cast.name
                                            page.update()

                                    is_tv_or_projector = "mogo" in cast.name.lower() or "tv" in cast.name.lower() or "screen" in cast.name.lower()
                                    device_list_column.controls.append(
                                        ft.ElevatedButton(
                                            text=cast.name,
                                            icon=ft.Icons.TV if is_tv_or_projector else ft.Icons.CAST,
                                            style=ft.ButtonStyle(
                                                bgcolor=ft.Colors.RED_800,
                                                color=ft.Colors.WHITE,
                                                padding=12
                                            ),
                                            on_click=trigger_cast
                                        )
                                    )
                            page.update()
                        except ImportError:
                            device_list_column.controls.clear()
                            device_list_column.controls.append(
                                ft.Text("Le module 'pychromecast' est manquant.", size=11, color=ft.Colors.RED_400)
                            )
                            page.update()
                        except Exception as scan_err:
                            device_list_column.controls.clear()
                            device_list_column.controls.append(
                                ft.Text(f"Erreur de scan : {scan_err}", size=11, color=ft.Colors.RED_400)
                            )
                            page.update()

                    def start_scan():
                        device_list_column.controls.clear()
                        device_list_column.controls.append(
                            ft.Row([
                                ft.ProgressRing(width=20, height=20, stroke_width=2.5, color=ft.Colors.RED_ACCENT),
                                ft.Text("Recherche d'écrans...", size=12, color=ft.Colors.GREY_400)
                            ], alignment=ft.MainAxisAlignment.CENTER)
                        )
                        page.update()
                        threading.Thread(target=scan_cast_devices, daemon=True).start()

                    # Fenêtre de Dialogue double-volet
                    cast_dialog = ft.AlertDialog(
                        title=ft.Row([
                            ft.Icon(ft.Icons.CAST, color=ft.Colors.RED_ACCENT),
                            ft.Text("Centre de Cast Universel", size=18, weight=ft.FontWeight.BOLD)
                        ], alignment=ft.MainAxisAlignment.START),
                        content=ft.Container(
                            content=ft.Row([
                                ft.Container(
                                    content=ft.Column([
                                        ft.Text("Écrans AndroidTV / Cast", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_ACCENT),
                                        ft.Divider(color=ft.Colors.GREY_800),
                                        ft.Container(content=device_list_column, alignment=ft.alignment.center, expand=True)
                                    ], spacing=10),
                                    width=280,
                                    height=320,
                                    padding=15,
                                    border=ft.border.all(1, ft.Colors.GREY_800),
                                    border_radius=12,
                                ),
                                ft.VerticalDivider(color=ft.Colors.GREY_800),
                                ft.Container(
                                    content=ft.Column([
                                        ft.Text("Écosystème Apple / Mobile / Multi-OS", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_ACCENT),
                                        ft.Divider(color=ft.Colors.GREY_800),
                                        qr_widget,
                                        ft.Text(
                                            "Scannez pour lire sur iPhone, Android (S9), Mac ou Linux. (FastAPI doit écouter sur 0.0.0.0)",
                                            size=10,
                                            color=ft.Colors.GREY_400,
                                            text_align=ft.TextAlign.CENTER
                                        )
                                    ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                    width=280,
                                    height=320,
                                    padding=15,
                                    border=ft.border.all(1, ft.Colors.GREY_800),
                                    border_radius=12,
                                )
                            ], alignment=ft.MainAxisAlignment.CENTER, spacing=15),
                            width=610,
                            height=340,
                        ),
                        actions=[
                            ft.TextButton(
                                "Fermer",
                                on_click=lambda e: page.close(cast_dialog),
                                style=ft.ButtonStyle(color=ft.Colors.WHITE)
                            )
                        ],
                        actions_alignment=ft.MainAxisAlignment.END,
                        modal=True,
                    )

                    page.open(cast_dialog)
                    start_scan()

                elif target_destination == "EXTERNAL":
                    webbrowser.open(play_url)

                # Restauration de l'état du bouton d'origine
                e.control.text = "🎬 Bande-annonce"
                e.control.update()
                page.update()

            except Exception as ex:
                try:
                    page.close(loading_dialog)
                except Exception:
                    pass
                page.open(
                    ft.SnackBar(
                        ft.Text(f"Erreur vidéo : {ex}"),
                        bgcolor=ft.Colors.RED_800,
                    )
                )
                e.control.text = "🎬 Bande-annonce"
                e.control.update()
                page.update()

        # 5. Dialogue de Configuration Initial (Interface Intégrée Fluide)
        lang_dropdown = ft.Dropdown(
            label="Langue de restitution",
            options=[
                ft.dropdown.Option("VF", "Français (VF)"),
                ft.dropdown.Option("VO", "Version Originale (VO)"),
                ft.dropdown.Option("VOSTFR", "Sous-titré Français (VOSTFR)"),
            ],
            value="VF",
            width=260,
            border_color=ft.Colors.GREY_700,
            focused_border_color=ft.Colors.RED_ACCENT,
        )

        quality_dropdown = ft.Dropdown(
            label="Qualité maximale désirée",
            options=[
                ft.dropdown.Option("2160", "4K Ultra HD (2160p)"),
                ft.dropdown.Option("1440", "2K Quad HD (1440p)"),
                ft.dropdown.Option("1080", "Full HD (1080p)"),
                ft.dropdown.Option("720", "HD Standard (720p)"),
            ],
            value="1080",
            width=260,
            border_color=ft.Colors.GREY_700,
            focused_border_color=ft.Colors.RED_ACCENT,
        )

        def submit_choice(destination):
            chosen_lang = lang_dropdown.value
            chosen_quality = quality_dropdown.value
            page.close(config_dialog)
            # Lancement immédiat du traitement en tâche de fond avec les nouveaux filtres
            threading.Thread(target=worker, args=(chosen_lang, chosen_quality, destination), daemon=True).start()

        # Construction graphique du sélecteur sans casser l'immersion visuelle
        config_dialog = ft.AlertDialog(
            title=ft.Text("Options de Visionnage", size=18, weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [
                    ft.Text("Personnalisez votre flux multimédia haute définition :", size=13, color=ft.Colors.GREY_400),
                    lang_dropdown,
                    quality_dropdown,
                    ft.Divider(color=ft.Colors.GREY_800),
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                "Lire ici",
                                icon=ft.Icons.PLAY_ARROW,
                                style=ft.ButtonStyle(bgcolor=ft.Colors.RED_800, color=ft.Colors.WHITE),
                                on_click=lambda _: submit_choice("INTERNAL"),
                            ),
                            ft.ElevatedButton(
                                "Cast TV/Mobile",
                                icon=ft.Icons.CAST,
                                on_click=lambda _: submit_choice("CAST"),
                            ),
                            ft.IconButton(
                                icon=ft.Icons.OPEN_IN_NEW,
                                tooltip="Navigateur externe (Lecteur OS)",
                                on_click=lambda _: submit_choice("EXTERNAL"),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=10,
                    ),
                ],
                tight=True,
                spacing=15,
            ),
            modal=False,
        )

        # 6. Définition de l'UI de chargement d'origine (conservée intégralement)
        loading_dialog = ft.AlertDialog(
            title=ft.Text("Chargement du flux HD...", size=16, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.ProgressRing(color=ft.Colors.RED_ACCENT),
                        ft.Text("Négociation du manifeste HLS...", size=12, color=ft.Colors.GREY_400),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=15,
                ),
                alignment=ft.alignment.center,
                height=130,
                width=240,
            ),
            modal=True,
        )

        # Ouverture de la première étape de l'interface (Sélection)
        page.open(config_dialog)

    # ==========================================
    # MODULE SOUS-TITRES
    # ==========================================
    def open_subtitle_search_dialog(e):
        """Ouvre le dialogue de recherche de sous-titres."""
        if not current_selected_video:
            show_snackbar("Sélectionnez d'abord un film.")
            return

        selected_lang = language_dropdown.value or "fr"
        video = current_selected_video
        video_path = video[3] if current_library_source == "local" else None
        title = video[1]
        year = video[2] or ""

        results_column = ft.Column(scroll="auto", spacing=8, height=320)
        status_text = ft.Text("🔍 Analyse du fichier et requêtes en cours...", color="grey400", italic=True)
        progress = ft.ProgressBar(width=400, color="#E50914", visible=True)

        if video_path and os.path.exists(video_path):
            dest_folder = os.path.dirname(video_path)
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            folder_writable = os.access(dest_folder, os.W_OK)
        else:
            dest_folder = APP_DATA_DIR
            base_name = re.sub(r'[\\/*?:"<>|]', "_", title)
            folder_writable = True

        if not folder_writable: dest_folder = APP_DATA_DIR

        dest_text = ft.Text(f"📁 Destination : {dest_folder}", size=12, color="grey400", italic=True)
        lang_label = dict(SUBTITLE_LANGUAGES).get(selected_lang, selected_lang)

        subtitle_dialog = ft.AlertDialog(
            title=ft.Text(f"🎬 Sous-titres — {title} [{lang_label}]", size=18, weight="bold", color="white"),
            bgcolor="#1a1a2e",
            content=ft.Column([
                dest_text, progress, status_text, ft.Divider(color="grey800"), results_column
            ], width=600, spacing=10),
            actions=[ft.TextButton("Fermer", on_click=lambda e: page.close(subtitle_dialog), style=ft.ButtonStyle(color="white"))]
        )

        acc_cfg = config.get("subtitle_accounts", {})
        engine_cfg = {
            "opensubtitles": acc_cfg.get("opensubtitles", {}),
            "user_agent": config.get("opensubtitles_user_agent", "OmniCine")
        }
        subtitle_sources = config.get("subtitle_sources", ["subtitlecat", "opensubtitles"])
        engine = SmartSubtitleEngine(config.get("tmdb_api_key"), engine_cfg, subtitle_sources)

        def build_result_row(result, index):
            svc_color = {"OpenSubtitles": "#E50914", "SubtitleCat": "#2196F3"}.get(result["service"], "#888")
            display_name = result.get("release") or result.get("file_name")
            
            dl_btn = ft.ElevatedButton(
                content=ft.Row([ft.Icon("download", size=16), ft.Text("Obtenir", size=13)], tight=True),
                style=ft.ButtonStyle(bgcolor={"": "#E50914"}, color={"": "white"}, shape=ft.RoundedRectangleBorder(radius=6))
            )

            def do_download(e):
                dl_btn.disabled = True
                dl_btn.content = ft.Row([ft.ProgressRing(width=14, height=14, stroke_width=2), ft.Text("...", size=13)], tight=True)
                page.update()
                def run_dl():
                    out_path, err = engine.download_and_extract(result, dest_folder, base_name, selected_lang)
                    if out_path:
                        dl_btn.content = ft.Row([ft.Icon("check", size=16, color="#4caf50"), ft.Text("OK", size=13)], tight=True)
                        dl_btn.style = ft.ButtonStyle(bgcolor={"": "#1b5e20"}, color={"": "white"})
                        show_snackbar(f"✅ Fichier sauvegardé : {os.path.basename(out_path)}", "#4caf50")
                    else:
                        dl_btn.disabled = False
                        dl_btn.content = ft.Row([ft.Icon("error", size=16, color="white"), ft.Text("Échec", size=13)], tight=True)
                        show_snackbar(f"❌ Erreur : {err}")
                    page.update()
                threading.Thread(target=run_dl, daemon=True).start()

            dl_btn.on_click = do_download

            return ft.Container(
                content=ft.Row([
                    ft.Container(content=ft.Text(result["service"], size=10, weight="bold", color="white"), bgcolor=svc_color, border_radius=4, padding=ft.padding.symmetric(4, 6), width=80, alignment=ft.alignment.center),
                    ft.Column([
                        ft.Text(display_name[:55] + "..." if len(display_name)>55 else display_name, size=12, weight="bold", color="white"),
                        ft.Text(f"⭐ Score: {int(result.get('_score',0))} | ⬇️ {result.get('downloads',0)}", size=11, color="grey400")
                    ], expand=True, spacing=1),
                    dl_btn
                ], alignment="center", spacing=10),
                bgcolor=ft.Colors.with_opacity(0.15, "white"), border_radius=8, padding=ft.padding.symmetric(8, 10),
                border=ft.border.all(1, ft.Colors.with_opacity(0.1, "white"))
            )

        def run_search():
            found = engine.search_all(video_path, title, year, selected_lang)
            progress.visible = False
            if found:
                status_text.value = f"✅ {len(found)} résultat(s) trié(s) par pertinence"
                status_text.color = "#4caf50"
                for idx, r in enumerate(found):
                    results_column.controls.append(build_result_row(r, idx))
            else:
                log_str = " | ".join(engine.logs[:3])
                status_text.value = f"😕 Aucun résultat. Logs: {log_str}" if engine.logs else "😕 Aucun sous-titre trouvé."
                status_text.color = "#ffc107"
            page.update()

        page.open(subtitle_dialog)
        threading.Thread(target=run_search, daemon=True).start()

    subtitles_button = ft.ElevatedButton(
        content=ft.Row([ft.Icon("subtitles"), ft.Text("🔍 Chercher Sous-titres")], alignment="center"),
        on_click=open_subtitle_search_dialog,
        disabled=True,
        style=ft.ButtonStyle(bgcolor={"": "#1565C0"}, color={"": "white"}, shape=ft.RoundedRectangleBorder(radius=8)),
        opacity=config.get("buttons_opacity", 1.0)
    )

    # ==========================================
    # FONCTIONS DE LECTURE VIDÉO
    # ==========================================
    def attendre_fin_lecteur_externe(player_path, video_path, video_id):
        """
        [PARACHUTE EXTERNE]
        Surveille en arrière-plan la fermeture du lecteur externe sélectionné
        pour proposer le nettoyage de façon fluide et asynchrone.
        """
        import threading
        import time
        import subprocess
        import os

        def monitor():
            time.sleep(3)  # Temps de latence pour laisser le lecteur s'ouvrir
            player_exe = os.path.basename(player_path).lower()

            while True:
                time.sleep(2)
                is_running = False
                try:
                    if os.name == 'nt':  # Windows
                        output = subprocess.check_output(
                            f'tasklist /FI "IMAGENAME eq {player_exe}"',
                            shell=True,
                            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                        ).decode('utf-8', errors='ignore')
                        if player_exe in output.lower():
                            is_running = True
                    else:  # Unix/Linux/macOS
                        output = subprocess.check_output(['ps', '-ax'], stderr=subprocess.DEVNULL).decode('utf-8', errors='ignore')
                        if player_exe in output.lower():
                            is_running = True
                except Exception:
                    # Sortie de secours immédiate en cas d'erreur système inattendue
                    break

                if not is_running:
                    break

            # On réinjecte l'affichage de l'alerte sur le thread principal Flet de manière sécurisée
            page.run_task(run_clean_async, video_path, video_id)

        threading.Thread(target=monitor, daemon=True).start()

    def proposer_nettoyage_hybride(video_path, video_id):
        """
        [PARACHUTE DE NETTOYAGE & DOUBLE SÉCURITÉ]
        Propose la suppression physique du fichier vidéo, de TOUS ses sous-titres,
        le nettoyage de la base de données et le rafraîchissement dynamique de l'UI.
        """
        import os
        import shutil
        import sqlite3
        import inspect

        # Parachute de base : Si le fichier a déjà été déplacé ou supprimé manuellement
        if not os.path.exists(video_path):
            return

        try:
            # Récupération propre du dossier et des fichiers associés
            dossier, nom_fichier = os.path.split(video_path)
            nom_sans_ext, _ = os.path.splitext(nom_fichier)
            fichiers_a_supprimer = [video_path]

            # Détection et ajout de toutes les extensions de sous-titres possibles (minuscule et majuscule)
            extensions_sub = [".srt", ".vtt", ".ass", ".sub"]
            for ext in extensions_sub:
                for suffix in [ext, ext.upper()]:
                    sub_path = os.path.join(dossier, nom_sans_ext + suffix)
                    if os.path.exists(sub_path) and sub_path not in fichiers_a_supprimer:
                        fichiers_a_supprimer.append(sub_path)

            # Calcul de la taille totale à libérer
            taille_totale = sum(os.path.getsize(f) for f in fichiers_a_supprimer)
            taille_go = taille_totale / (1024 ** 3)

            # Calcul de l'espace libre actuel sur le disque concerné
            try:
                usage = shutil.disk_usage(dossier if dossier else os.getcwd())
                espace_libre_go = usage.free / (1024 ** 3)
            except Exception:
                espace_libre_go = 0.0

            # Action de suppression finale sécurisée
            def action_supprimer(e):
                try:
                    page.close(dialog_nettoyage)
                except Exception:
                    pass

                fichiers_supprimes_avec_succes = 0
                for f in fichiers_a_supprimer:
                    if os.path.exists(f):
                        try:
                            os.remove(f)
                            fichiers_supprimes_avec_succes += 1
                        except Exception as err_del:
                            print(f"[Nettoyage] Erreur lors de la suppression de {f} : {err_del}")

                if fichiers_supprimes_avec_succes > 0:
                    # Parachute de base de données : Recherche automatique de votre base SQLite
                    try:
                        db_candidates = ["omnicine.db", "database.db", "library.db", "videos.db"]
                        db_path = None
                        for db in db_candidates:
                            if os.path.exists(db):
                                db_path = db
                                break
                        if db_path:
                            conn = sqlite3.connect(db_path)
                            cursor = conn.cursor()
                            cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
                            conn.commit()
                            conn.close()
                    except Exception as db_err:
                        print(f"[Nettoyage DB] Échec de la suppression en base de données : {db_err}")

                    # Parachute UI : Détection et exécution dynamique de votre fonction de rafraîchissement
                    refreshed = False
                    fonctions_recherchees = ["update_gallery", "refresh_gallery", "load_library", "load_videos", "show_library"]
                    for frame_info in inspect.stack():
                        frame = frame_info.frame
                        for name in fonctions_recherchees:
                            # Recherche dans les variables locales de la pile
                            if name in frame.f_locals and callable(frame.f_locals[name]):
                                try:
                                    frame.f_locals[name]()
                                    refreshed = True
                                    break
                                except Exception:
                                    pass
                            # Recherche globale si non trouvé localement
                            if name in frame.f_globals and callable(frame.f_globals[name]):
                                try:
                                    frame.f_globals[name]()
                                    refreshed = True
                                    break
                                except Exception:
                                    pass
                        if refreshed:
                            break

                    show_snackbar(f"🎉 Nettoyage réussi ! +{taille_go:.2f} Go libérés.")
                else:
                    show_snackbar("⚠️ Impossible de supprimer les fichiers (fichiers verrouillés ou en cours d'utilisation).")

            # Création de la boîte de dialogue d'alerte Flet 0.27.6
            dialog_nettoyage = ft.AlertDialog(
                title=ft.Text("🎬 Fin de visionnage"),
                content=ft.Text(
                    f"Voulez-vous supprimer définitivement ce film ainsi que tous ses fichiers de sous-titres associés ?\n\n"
                    f"📊 Espace à libérer : {taille_go:.2f} Go\n"
                    f"💽 Espace libre actuel : {espace_libre_go:.2f} Go"
                ),
                actions=[
                    ft.TextButton("Conserver", on_click=lambda e: page.close(dialog_nettoyage)),
                    ft.ElevatedButton("Supprimer définitivement", bgcolor="#E50914", color="white", on_click=action_supprimer),
                ],
            )
            page.open(dialog_nettoyage)

        except Exception as err_prep:
            print(f"[Nettoyage Parachute] Erreur lors de la préparation : {err_prep}")

    def play_video(video_data=None, force_cast=False):
        """Lance la lecture vidéo selon la préférence de l'utilisateur ou ouvre le menu Cast."""
        nonlocal current_selected_video
        vid = video_data if (video_data and not isinstance(video_data, str)) else current_selected_video
        if not vid: return
        
        # Déclenchement immédiat si l'utilisateur clique sur le symbole du Cast dédié
        if force_cast or video_data == "cast":
            # ==========================================
            # CENTRE DE CAST UNIVERSEL - FILMS & SÉRIES
            # ==========================================
            import socket
            import threading
            import io
            import base64
            import flet as ft

            # 1. Résolution de l'adresse IP locale sur la Box 4G
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                try: local_ip = socket.gethostbyname(socket.gethostname())
                except Exception: local_ip = "127.0.0.1"

            port = config.get('server_port', 8080)
            network_video_url = f"http://{local_ip}:{port}/stream/{vid[0]}"
            search_title = vid[1] if len(vid) > 1 else "Film OmniCine"

            # 2. Génération du QR Code universel
            try:
                import qrcode
                qr = qrcode.QRCode(version=1, box_size=10, border=4)
                qr.add_data(network_video_url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                qr_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                qr_widget = ft.Image(src_base64=qr_base64, width=180, height=180, fit=ft.ImageFit.CONTAIN)
            except Exception as qr_err:
                qr_widget = ft.Column([
                    ft.Icon(ft.Icons.QR_CODE_SCANNER, size=80, color=ft.Colors.RED_400),
                    ft.Text(f"Erreur QR : {qr_err}", size=11, color=ft.Colors.RED_400)
                ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

            # 3. Conteneur de réception des périphériques Cast
            device_list_column = ft.Column(controls=[], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10, scroll=ft.ScrollMode.AUTO)

            # 4. Scanner Multicast pour la Box 4G
            def scan_cast_devices():
                try:
                    import pychromecast
                    chromecasts, browser = pychromecast.get_chromecasts(timeout=4)
                    pychromecast.discovery.stop_discovery(browser)
                    device_list_column.controls.clear()
                    
                    if not chromecasts:
                        device_list_column.controls.append(
                            ft.Text("Aucun écran Cast détecté sur la Box 4G.", size=12, color=ft.Colors.GREY_500, italic=True)
                        )
                        device_list_column.controls.append(
                            ft.IconButton(icon=ft.Icons.REFRESH, icon_color=ft.Colors.RED_ACCENT, on_click=lambda _: start_scan())
                        )
                    else:
                        device_list_column.controls.append(
                            ft.Text(f"{len(chromecasts)} écran(s) disponible(s) :", size=12, color=ft.Colors.GREY_400)
                        )
                        for cast in chromecasts:
                            def trigger_cast(e, target_cast=cast):
                                e.control.disabled = True
                                e.control.text = "Connexion..."
                                page.update()
                                try:
                                    target_cast.wait(timeout=5)
                                    media_ctrl = target_cast.media_controller
                                    media_ctrl.play_media(network_video_url, "video/mp4", title=f"OmniCine - {search_title}")
                                    media_ctrl.block_until_active(timeout=5)
                                    show_snackbar(f"Diffusion lancée sur : {target_cast.name}")
                                    page.close(cast_dialog)
                                except Exception as play_err:
                                    show_snackbar(f"Erreur Cast : {play_err}")
                                    e.control.disabled = False
                                    e.control.text = target_cast.name
                                    page.update()

                            is_tv_or_projector = "mogo" in cast.name.lower() or "tv" in cast.name.lower() or "screen" in cast.name.lower()
                            device_list_column.controls.append(
                                ft.ElevatedButton(
                                    text=cast.name,
                                    icon=ft.Icons.TV if is_tv_or_projector else ft.Icons.CAST,
                                    style=ft.ButtonStyle(bgcolor=ft.Colors.RED_800, color=ft.Colors.WHITE, padding=12),
                                    on_click=trigger_cast
                                )
                            )
                    page.update()
                except Exception as scan_err:
                    device_list_column.controls.clear()
                    device_list_column.controls.append(ft.Text(f"Erreur réseau : {scan_err}", size=11, color=ft.Colors.RED_400))
                    page.update()

            def start_scan():
                device_list_column.controls.clear()
                device_list_column.controls.append(
                    ft.Row([
                        ft.ProgressRing(width=20, height=20, stroke_width=2.5, color=ft.Colors.RED_ACCENT),
                        ft.Text("Recherche...", size=12, color=ft.Colors.GREY_400)
                    ], alignment=ft.MainAxisAlignment.CENTER)
                )
                page.update()
                threading.Thread(target=scan_cast_devices, daemon=True).start()

            # 5. Interface de dialogue double volet
            cast_dialog = ft.AlertDialog(
                title=ft.Row([ft.Icon(ft.Icons.CAST, color=ft.Colors.RED_ACCENT), ft.Text(f"Cast Vidéo : {search_title}", size=16, weight=ft.FontWeight.BOLD)]),
                content=ft.Container(
                    content=ft.Row([
                        ft.Container(content=ft.Column([ft.Text("Écrans Cast / Android TV", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_ACCENT), ft.Divider(color=ft.Colors.GREY_800), ft.Container(content=device_list_column, alignment=ft.alignment.center, expand=True)], spacing=10), width=280, height=320, padding=15, border=ft.border.all(1, ft.Colors.GREY_800), border_radius=12),
                        ft.VerticalDivider(color=ft.Colors.GREY_800),
                        ft.Container(content=ft.Column([ft.Text("Lecteur Web Universel", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.RED_ACCENT), ft.Divider(color=ft.Colors.GREY_800), qr_widget, ft.Text("Scannez pour lire sur votre smartphone ou tablette.", size=10, color=ft.Colors.GREY_400, text_align=ft.TextAlign.CENTER)], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER), width=280, height=320, padding=15, border=ft.border.all(1, ft.Colors.GREY_800), border_radius=12)
                    ], alignment=ft.MainAxisAlignment.CENTER, spacing=15), width=610, height=340
                ),
                actions=[ft.TextButton("Fermer", on_click=lambda e: page.close(cast_dialog), style=ft.ButtonStyle(color=ft.Colors.WHITE))],
                actions_alignment=ft.MainAxisAlignment.END
            )
            page.open(cast_dialog)
            start_scan()
            return

        preferred_player = config.get("preferred_player", "external")
        
        if preferred_player == "native":
            # Utiliser le lecteur natif ft.Video
            video_path = vid[3] if current_library_source == "local" else f"http://127.0.0.1:{config.get('server_port', 8080)}/stream/{vid[0]}"
            native_video_player.src = video_path
            native_video_player.visible = True

            # Branchement de l'événement de fin de lecture natif (uniquement pour le stockage local)
            if current_library_source == "local":
                def on_native_completed(e):
                    try:
                        page.close(video_player_dialog)
                    except Exception:
                        pass
                    proposer_nettoyage_hybride(vid[3], vid[0])
                native_video_player.on_completed = on_native_completed
            else:
                native_video_player.on_completed = None

            page.open(video_player_dialog)
        else:
            # Utiliser un lecteur externe
            if not player_dropdown.value: return
            player_path = config["players"].get(player_dropdown.value)
            if not player_path or not os.path.exists(player_path):
                show_snackbar(f"Lecteur introuvable : {player_path}")
                return
            video_target = vid[3] if current_library_source == "local" else f"http://127.0.0.1:{config.get('server_port', 8080)}/stream/{vid[0]}"
            
            launch_video_process(player_path, video_target, page)

            # Branchement de la surveillance de fin de lecture externe (uniquement pour le stockage local)
            if current_library_source == "local":
                attendre_fin_lecteur_externe(player_path, vid[3], vid[0])

    def play_hover(e):
        """Effet hover sur le bouton play."""
        if play_button.disabled: return
        if "LANCER" in getattr(e.control, "data", ""):
            e.control.gradient = ft.LinearGradient(colors=["#ff1e27", "#b3070f"]) if e.data == "true" else ft.LinearGradient(colors=["#E50914", "#9B060C"])
            e.control.shadow = ft.BoxShadow(blur_radius=25, color="#E50914", offset=ft.Offset(0,0)) if e.data == "true" else None
            e.control.update()

    def cast_btn_hover(e):
        """Effet hover sur le bouton cast."""
        if play_button.disabled: return
        e.control.gradient = ft.LinearGradient(colors=["#333333", "#222222"]) if e.data == "true" else ft.LinearGradient(colors=["#1A1A1A", "#0A0A0A"])
        e.control.shadow = ft.BoxShadow(blur_radius=15, color="#E50914", offset=ft.Offset(0,0)) if e.data == "true" else None
        e.control.update()

    # Le conteneur parent regroupe maintenant les deux boutons physiques côte à côte
    play_button = ft.Container(
        content=ft.Row([
            # Bouton de lecture standard (prend toute la place disponible à gauche)
            ft.Container(
                content=ft.Row([ft.Icon("play_arrow", color="white", size=24), ft.Text("LANCER LE FILM", weight="bold", size=16)], alignment="center"),
                gradient=ft.LinearGradient(colors=["#E50914", "#9B060C"]),
                border_radius=8,
                padding=15,
                data="LANCER",
                on_hover=play_hover,
                on_click=lambda e: play_video(),
                expand=True
            ),
            # Bouton de Cast dédié (icône carrée à droite, style épuré avec contour rouge)
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.CAST, color="white", size=24)], alignment="center"),
                gradient=ft.LinearGradient(colors=["#1A1A1A", "#0A0A0A"]),
                border=ft.border.all(1, "#E50914"),
                border_radius=8,
                padding=15,
                width=60,
                on_hover=cast_btn_hover,
                on_click=lambda e: play_video(force_cast=True)
            )
        ], alignment="center", spacing=10),
        disabled=True,
        opacity=0.5 * config.get("buttons_opacity", 1.0),
        animate=ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)
    )

    # ==========================================
    # DIALOGUE DE DÉTAILS
    # ==========================================
    def show_details_dialog(video):
        """Affiche les détails complets d'un film."""
        is_local_img = current_library_source == "local"
        dialog = ft.AlertDialog(
            title=ft.Text(video[1], size=26, weight="bold", color="white"), bgcolor="#1f1f1f",
            content=ft.Column([
                ft.Row([
                    ft.Image(src=video[7], width=140, height=200, fit=ft.ImageFit.COVER, border_radius=6) if (video[7] and (not is_local_img or os.path.exists(video[7]))) else ft.Icon("movie", size=60, color="grey"),
                    ft.Column([
                        ft.Text(f"Année : {video[2] or 'N/A'}", weight="bold", color="white"),
                        ft.Text(f"Réalisateur : {video[9] or 'Inconnu'}", color="grey300"),
                        ft.Text(f"Durée : {video[11] or 'N/A'}", color="grey300"),
                        ft.Text(f"Note : {video[5] or 'N/A'}", color="#ffc107", weight="bold"),
                    ], spacing=6, expand=True)
                ]),
                ft.Text(f"Genres : {video[10] or 'N/A'}", italic=True, color="#E50914"),
                ft.Text(video[4], color="grey300")
            ], scroll="auto", tight=True, width=580),
            actions=[ft.TextButton("Fermer", on_click=lambda e: page.close(dialog), style=ft.ButtonStyle(color="white"))]
        )
        page.open(dialog)

    # ==========================================
    # SÉLECTION DE VIDÉO
    # ==========================================
    def select_video(video):
        """Sélectionne une vidéo et affiche ses détails."""
        nonlocal current_selected_video, current_trailers
        current_selected_video = video
        title_text.value = video[1]
        rating_text.value = f"Note des spectateurs : {video[5]}"
        cast_text.value = video[6]
        synopsis_text.value = video[4]
        is_local = (current_library_source == "local")
        
        # Récupérer les bandes-annonces depuis les métadonnées TMDB
        # Note: Les trailers sont stockés dans la DB ou récupérés à la volée
        current_trailers = []  # Sera rempli si on a les données
        
        if video[7] and (not is_local or os.path.exists(video[7])):
            poster_image.src = video[7]; poster_image.src_base64 = None; poster_image.visible = True
        else:
            poster_image.src_base64 = TRANSPARENT_PLACEHOLDER; poster_image.visible = False
            
        if video[8] and (not is_local or os.path.exists(video[8])):
            app_bg.image = ft.DecorationImage(src=video[8], fit=ft.ImageFit.COVER)
            app_bg.opacity = config.get("ui_opacity", 0.55)
        else:
            app_bg.image = None
            app_bg.opacity = 0.0
            
        play_button.disabled = False
        play_button.opacity = config.get("buttons_opacity", 1.0)
        subtitles_button.disabled = False
        subtitles_button.opacity = config.get("buttons_opacity", 1.0)
        # Note: trailer_button sera défini plus tard, on ne le modifie pas ici
        page.update()

    # ==========================================
    # GALERIE AVEC BOUTON LECTURE RAPIDE ET APERÇU VIDÉO
    # ==========================================
    gallery_grid = ft.GridView(expand=True, max_extent=300, child_aspect_ratio=0.67, spacing=20, run_spacing=20, opacity=config.get("gallery_opacity", 1.0))
    server_selector = ft.Dropdown(width=250, border_color="#E50914", value="local", on_change=lambda e: change_library_source(e.control.value))

    def normalize_movie_data(vid):
        """Convertit la structure tuple ou dictionnaire en objet dictionnaire standard."""
        if isinstance(vid, dict):
            return vid
        if isinstance(vid, (tuple, list)):
            return {
                "id": vid[0] if len(vid) > 0 else None,
                "title": vid[1] if len(vid) > 1 else "",
                "year": vid[2] if len(vid) > 2 else None,
                "filepath": vid[3] if len(vid) > 3 else None,
                "local_path": vid[3] if len(vid) > 3 else None,
                "file_path": vid[3] if len(vid) > 3 else None,
                "synopsis": vid[4] if len(vid) > 4 else None,
                "poster_url": vid[7] if len(vid) > 7 else None,
                "backdrop_url": vid[8] if len(vid) > 8 else None,
                "genres": vid[10] if len(vid) > 10 else None,
                "trailer_url": vid[14] if len(vid) > 14 else None,
                "video_url": vid[3] if len(vid) > 3 else None,
                "media_type": vid[16] if len(vid) > 16 else "movie"
            }
        return {}

    def refresh_server_selector_options():
        server_selector.options = [ft.dropdown.Option("local", "📍 Ce Cinéma")]
        for srv in get_federated_servers():
            server_selector.options.append(ft.dropdown.Option(srv[2], f"🌐 {srv[1]}"))
        page.update()

    def change_library_source(source_value):
        nonlocal current_library_source
        current_library_source = source_value
        update_gallery()

    def card_hover(e):
        e.control.scale = 1.05 if str(e.data).lower() in ("true", "1") else 1.0
        e.control.shadow = ft.BoxShadow(
            blur_radius=25,
            color="#E50914" if str(e.data).lower() in ("true", "1") else ft.Colors.TRANSPARENT,
            offset=ft.Offset(0, 0)
        )
        e.control.update()

    def quick_play(e, video):
        """Lecture rapide depuis la galerie."""
        select_video(video)
        play_video(video)

    def update_gallery():
        """Met à jour la galerie de films avec détection et aperçu vidéo au survol."""
        gallery_grid.controls.clear()
        
        # Récupération sécurisée du filtre actif dans la session ("Tous", "Films", "Séries")
        current_filter = page.session.get("gallery_filter") or "Tous"
        # Récupération sécurisée du filtre de genre ("Tous" ou "Action", "Thriller", etc.)
        current_genre = page.session.get("active_genre_filter") or "Tous"
        
        if current_library_source == "local":
            videos = get_all_local_videos()
        else:
            try:
                resp = requests.get(f"{current_library_source}/library", timeout=5)
                if resp.status_code == 200:
                    videos = [(item["id"], item["title"], item["year"], item["filepath"], item["synopsis"], item["rating"], item["cast"], f"{current_library_source}/poster/{item['id']}", f"{current_library_source}/backdrop/{item['id']}", item["director"], item["genres"], item["runtime"], item["actors"], item["country"], None, None, item.get("media_type", "movie")) for item in resp.json()]
                else: videos = []
            except Exception: videos = []

        # TRI ALPHABÉTIQUE (A-Z) SUR LE TITRE DE LA VIDÉO (INDEX 1)
        videos = sorted(videos, key=lambda v: (v[1] or "").lower())

        for vid in videos:
            movie_dict = normalize_movie_data(vid)

            # Gestion de la longueur de la structure pour extraire le media_type de manière stable
            m_type = vid[16] if len(vid) > 16 else "movie"
            # Extraction des genres (Index 10 dans la structure)
            video_genres = vid[10] if (len(vid) > 10 and vid[10]) else ""
            
            # Application stricte du filtre de type (Films / Séries)
            if current_filter == "Films" and m_type != "movie":
                continue
            elif current_filter == "Séries" and m_type != "tv":
                continue
            
            # Application stricte du filtre de genre (Action, Thriller, etc.)
            if current_genre != "Tous" and current_genre not in [g.strip() for g in video_genres.split(",")]:
                continue

            is_local = (current_library_source == "local")
            p_path = vid[7] if (vid[7] and (not is_local or os.path.exists(vid[7]))) else None
            
            # Conteneur superposé pour le lecteur d'aperçu vidéo
            card_video_container = ft.Container(visible=False, expand=True)

            # Bouton de lecture rapide (overlay)
            quick_play_btn = ft.IconButton(
                icon=ft.Icons.PLAY_ARROW,
                icon_color="white",
                icon_size=32,
                bgcolor=ft.Colors.with_opacity(0.7, "#E50914"),
                style=ft.ButtonStyle(shape=ft.CircleBorder()),
                visible=False,
                on_click=lambda e, v=vid: quick_play(e, v)
            )
            
            card_content = ft.Container(
                content=ft.Stack([
                    ft.Container(
                        content=ft.Image(src=p_path, fit=ft.ImageFit.COVER, border_radius=12) if p_path else ft.Icon("movie", size=50, color="white"), 
                        alignment=ft.alignment.center if not p_path else None, 
                        bgcolor="#1f1f1f" if not p_path else None, 
                        top=0, bottom=0, left=0, right=0
                    ),
                    ft.Container(
                        gradient=ft.LinearGradient(
                            begin=ft.alignment.top_center, 
                            end=ft.alignment.bottom_center, 
                            colors=["transparent", "#CC000000"]
                        ), 
                        top=0, bottom=0, left=0, right=0, border_radius=12
                    ),
                    ft.Container(
                        content=ft.Text(
                            vid[1], 
                            size=14 if not config.get("big_picture", False) else 18, 
                            weight="bold", 
                            text_align="center", 
                            max_lines=2, 
                            overflow="ellipsis", 
                            color="white"
                        ), 
                        padding=10, bottom=0, left=0, right=0
                    ),
                    card_video_container,
                    ft.Container(content=quick_play_btn, alignment=ft.alignment.center, top=0, bottom=0, left=0, right=0)
                ]),
                border_radius=12, scale=1.0, 
                animate_scale=ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT),
                animate=ft.animation.Animation(400, ft.AnimationCurve.EASE_IN_OUT)
            )

            # Logique d'extraction de couleur, d'animation de pulsation ET déclenchement de l'aperçu au survol
            def setup_hover_effect(container, btn, img_path, title, m_dict, video_box):
                def get_dominant_color():
                    try:
                        from PIL import Image
                        if img_path and os.path.exists(img_path):
                            with Image.open(img_path) as img:
                                img = img.resize((1, 1))
                                c = img.getpixel((0, 0))
                                return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"
                    except Exception:
                        pass
                    palette = ["#E50914", "#00D2FF", "#FFD700", "#9D00FF", "#00FF87", "#FF007F", "#FF5722"]
                    return palette[abs(hash(title)) % len(palette)]

                dom_color = get_dominant_color()

                def on_hover_pulse(e):
                    # 1. TRANSMISSION À LA DÉTECTION ET AU LECTEUR D'APERÇU
                    handle_movie_hover(e, m_dict, video_box)

                    # 2. GESTION DU BOUTON QUICK PLAY
                    hover_active = (str(e.data).lower() in ("true", "1"))
                    btn.visible = hover_active
                    btn.update()
                    
                    # 3. ANIMATION DE PULSATION DE BORDURE
                    if hover_active:
                        container.data = "pulsing"
                        def pulse_loop():
                            toggle = True
                            while getattr(container, "data", "") == "pulsing":
                                container.border = ft.border.all(
                                    3.5 if toggle else 1.0, 
                                    dom_color if toggle else ft.Colors.with_opacity(0.15, dom_color)
                                )
                                try:
                                    container.update()
                                except Exception:
                                    break
                                time.sleep(0.4)
                                toggle = not toggle
                        threading.Thread(target=pulse_loop, daemon=True).start()
                    else:
                        container.data = "stopped"
                        container.border = None
                        try:
                            container.update()
                        except Exception:
                            pass
                return on_hover_pulse

            card_content.on_click = lambda e, v=vid: select_video(v)
            card_content.on_hover = setup_hover_effect(card_content, quick_play_btn, p_path, vid[1], movie_dict, card_video_container)
            
            gallery_grid.controls.append(
                ft.GestureDetector(
                    on_double_tap=lambda e, v=vid: show_details_dialog(v),
                    on_tap_down=lambda e, btn=quick_play_btn: setattr(btn, 'visible', True) or btn.update(),
                    on_tap_up=lambda e, btn=quick_play_btn: setattr(btn, 'visible', False) or btn.update(),
                    content=card_content
                )
            )
        page.update()

    # ==========================================
    # FONCTIONS DE SCAN ET NETTOYAGE (AUTOMATISÉES & MANUELLES)
    # ==========================================
    def run_maintenance_task(auto_mode=False):
        """Exécute séquentiellement le nettoyage et le scan avec mise à jour de la progression."""
        progress_container.visible = True
        progress_bar.value = 0.0
        progress_text.value = "[0%] Démarrage de la maintenance automatique..." if auto_mode else "[0%] Démarrage de la maintenance..."
        page.update()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # ------------------------------------------
        # PHASE 1 : NETTOYAGE DE LA BIBLIOTHÈQUE (0% -> 25%)
        # ------------------------------------------
        progress_text.value = "[10%] Analyse des fichiers introuvables..."
        page.update()
        
        cursor.execute("SELECT id, filepath FROM videos WHERE origin_server = 'local'")
        vids_to_check = cursor.fetchall()
        total_vids = len(vids_to_check)
        
        if total_vids > 0:
            for idx, (vid_id, filepath) in enumerate(vids_to_check):
                if "://" in filepath: continue
                if not os.path.exists(os.path.normpath(filepath)):
                    cursor.execute("DELETE FROM videos WHERE id=?", (vid_id,))
                # Progression fluide durant le nettoyage de 10% à 25%
                pct = 10 + int((idx / total_vids) * 15)
                progress_bar.value = pct / 100
                progress_text.value = f"[{pct}%] Nettoyage : vérification des fichiers de la base..."
                page.update()
        
        conn.commit()
        progress_bar.value = 0.25
        progress_text.value = "[25%] Nettoyage terminé. Préparation du scan..."
        page.update()

        # ------------------------------------------
        # PHASE 2 : SCAN DES DOSSIERS LOCAUX (25% -> 70%)
        # ------------------------------------------
        folders = get_scanned_folders()
        total_folders = len(folders)
        
        if total_folders > 0:
            for f_idx, folder in enumerate(folders):
                if not os.path.exists(folder): continue
                progress_text.value = f"[35%] Scan du dossier : {os.path.basename(folder)}"
                page.update()
                
                for root, _, files in os.walk(folder):
                    for file in files:
                        if file.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm')):
                            filepath = os.path.normpath(os.path.join(root, file))
                            cursor.execute("SELECT id FROM videos WHERE filepath=?", (filepath,))
                            if cursor.fetchone(): continue
                            
                            clean_t, yr = clean_filename(file)
                            progress_text.value = f"[45%] Indexation locale : {clean_t}"
                            page.update()
                            
                            meta = fetch_metadata(clean_t, yr, config.get("tmdb_api_key"))
                            p_path = download_image(meta.get('poster'), f"poster_{clean_t}")
                            b_path = download_image(meta.get('backdrop'), f"backdrop_{clean_t}")
                            
                            cursor.execute("INSERT INTO videos (filepath, filename, title, year, synopsis, rating, poster_path, backdrop_path, cast_info, director, genres, runtime, actors, country, origin_server, tmdb_id, imdb_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local', ?, ?)", (filepath, file, meta.get('title'), meta.get('year') or yr, meta.get('synopsis'), meta.get('rating'), p_path, b_path, meta.get('cast'), meta.get('director'), meta.get('genres'), meta.get('runtime'), meta.get('actors'), meta.get('country'), meta.get('tmdb_id'), meta.get('imdb_id')))
                            conn.commit()
                
                # Progression de 25% à 70% selon le nombre de dossiers traités
                pct = 25 + int(((f_idx + 1) / total_folders) * 45)
                progress_bar.value = pct / 100
                page.update()

        # ------------------------------------------
        # PHASE 3 : SCAN DES DOSSIERS RÉSEAU (70% -> 100%)
        # ------------------------------------------
        storages = get_network_storages_full()
        total_storages = len(storages)
        
        if total_storages > 0:
            for s_idx, storage in enumerate(storages):
                st_id, st_name, st_type, st_host, st_user, st_pass, st_root = storage
                progress_text.value = f"[75%] Scan de l'espace réseau : {st_name} ({st_type})..."
                page.update()
                
                remote_files = []
                if st_type == "FTP": remote_files = get_remote_ftp_files(st_host, st_user, st_pass, st_root)
                elif st_type == "SFTP": remote_files = get_remote_sftp_files(st_host, st_user, st_pass, st_root)
                elif st_type == "SMB":
                    try:
                        client_name = socket.gethostname()
                        conn_smb = SMBConnection(st_user, st_pass, client_name, st_host, use_ntlm_v2=True)
                        if conn_smb.connect(st_host, 445, timeout=5):
                            share_name = st_root.strip("/\\").split("/")[0].split("\\")[0]
                            sub_folder = "/".join(st_root.strip("/\\").split("/")[1:]) if "/" in st_root.replace("\\", "/") else ""
                            def walk_smb(path):
                                try:
                                    for f in conn_smb.listPath(share_name, path):
                                        if f.filename in [".", ".."]: continue
                                        full_p = f"{path}/{f.filename}" if path else f.filename
                                        if f.isDirectory: walk_smb(full_p)
                                        elif f.filename.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm')):
                                            remote_files.append(f"smb://{st_host}/{share_name}/{full_p}")
                                except Exception: pass
                            walk_smb(sub_folder)
                            conn_smb.close()
                    except Exception: pass

                for item in remote_files:
                    cursor.execute("SELECT id FROM videos WHERE filepath=?", (item,))
                    if cursor.fetchone(): continue
                    filename = os.path.basename(item)
                    clean_t, yr = clean_filename(filename)
                    
                    progress_text.value = f"[85%] Indexation réseau : {clean_t}"
                    page.update()
                    
                    meta = fetch_metadata(clean_t, yr, config.get("tmdb_api_key"))
                    p_path = download_image(meta.get('poster'), f"poster_{clean_t}")
                    b_path = download_image(meta.get('backdrop'), f"backdrop_{clean_t}")
                    
                    cursor.execute("INSERT INTO videos (filepath, filename, title, year, synopsis, rating, poster_path, backdrop_path, cast_info, director, genres, runtime, actors, country, origin_server, tmdb_id, imdb_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local', ?, ?)", (item, filename, meta.get('title'), meta.get('year') or yr, meta.get('synopsis'), meta.get('rating'), p_path, b_path, meta.get('cast'), meta.get('director'), meta.get('genres'), meta.get('runtime'), meta.get('actors'), meta.get('country'), meta.get('tmdb_id'), meta.get('imdb_id')))
                    conn.commit()

                # Progression de 70% à 100% selon le nombre de stockages distants
                pct = 70 + int(((s_idx + 1) / total_storages) * 30)
                progress_bar.value = pct / 100
                page.update()

        conn.close()
        
        # Finalisation de la tâche
        progress_bar.value = 1.0
        progress_text.value = "[100%] Traitement terminé avec succès !"
        page.update()
        time.sleep(1.5) # Laisse le temps d'apprécier la fin du chargement
        
        progress_container.visible = False
        update_gallery()
        show_snackbar("Maintenance et indexation terminées !")

    def scan_library(e):
        """Déclenchement manuel du scan via l'UI."""
        threading.Thread(target=run_maintenance_task, args=(False,), daemon=True).start()

    def force_clean_library(e):
        """Déclenchement manuel du nettoyage seul (conserve la compatibilité)."""
        scan_library(e)

    # ==========================================
    # RÉGLAGES DE L'APPLICATION
    # ==========================================
    app_name_input = ft.TextField(label="Titre de votre espace cinéma", value=config.get("app_name", "OmniCine"), expand=True)
    tmdb_key_input = ft.TextField(label="Clé d'accès TMDB", value=config.get("tmdb_api_key", "96ec18a95517ccee42b88a7bce3ffe8d"), password=True, can_reveal_password=True, expand=True)
    server_port_input = ft.TextField(label="Canal réseau (Avancé)", value=str(config.get("server_port", 8080)), expand=True)
    
    def update_app_settings(e):
        config["app_name"] = app_name_input.value
        config["tmdb_api_key"] = tmdb_key_input.value or "96ec18a95517ccee42b88a7bce3ffe8d"
        try: config["server_port"] = int(server_port_input.value)
        except ValueError: config["server_port"] = 8080
        save_config(config); page.title = config["app_name"]; page.update()
        show_snackbar("Réglages sauvegardés.")

    def toggle_fullscreen(e=None):
        config["fullscreen"] = not config.get("fullscreen", False)
        page.window.full_screen = config["fullscreen"]
        switch_fullscreen.value = config["fullscreen"]
        save_config(config)
        page.update()

    def toggle_big_picture(e=None):
        config["big_picture"] = not config.get("big_picture", False)
        switch_big_picture.value = config["big_picture"]
        if config["big_picture"]:
            config["fullscreen"] = True
            page.window.full_screen = True
            switch_fullscreen.value = True
        save_config(config)
        apply_big_picture_dimensions()

    def apply_big_picture_dimensions():
        is_bp = config.get("big_picture", False)
        gallery_grid.max_extent = 300 if is_bp else 200
        gallery_grid.spacing = 35 if is_bp else 20
        gallery_grid.run_spacing = 35 if is_bp else 20
        poster_image.width = 380 if is_bp else 280
        poster_image.height = 570 if is_bp else 420
        title_text.size = 40 if is_bp else 28
        rating_text.size = 22 if is_bp else 15
        cast_text.size = 20 if is_bp else 14
        synopsis_text.size = 18 if is_bp else 14
        play_button.padding = 22 if is_bp else 15
        play_button.content.controls[0].size = 32 if is_bp else 24
        play_button.content.controls[1].size = 22 if is_bp else 16
        update_gallery()
        page.update()

    def update_backdrop_opacity(e):
        config["ui_opacity"] = e.control.value
        if app_bg.image is not None: app_bg.opacity = config["ui_opacity"]
        save_config(config); page.update()

    def update_details_opacity(e):
        config["details_opacity"] = e.control.value
        details_panel.bgcolor = ft.Colors.with_opacity(config["details_opacity"], ft.Colors.BLACK)
        save_config(config); page.update()

    def update_gallery_opacity(e):
        config["gallery_opacity"] = e.control.value
        gallery_grid.opacity = config["gallery_opacity"]
        save_config(config); page.update()

    def update_buttons_opacity(e):
        config["buttons_opacity"] = e.control.value
        if not play_button.disabled: play_button.opacity = config["buttons_opacity"]
        else: play_button.opacity = 0.5 * config["buttons_opacity"]
        subtitles_button.opacity = config["buttons_opacity"]
        # Note: trailer_button sera défini plus tard, on le mettra à jour après
        btn_nav_library.opacity = config["buttons_opacity"]
        btn_nav_settings.opacity = config["buttons_opacity"]
        save_config(config); page.update()

    def update_banner_opacity(e):
        config["banner_opacity"] = e.control.value
        genre_filter_container.bgcolor = ft.Colors.with_opacity(config["banner_opacity"], "#141414")
        save_config(config); page.update()

    def update_background_blur(e):
        config["background_blur"] = int(e.control.value)
        app_bg.blur = ft.Blur(config["background_blur"], config["background_blur"], ft.BlurTileMode.CLAMP)
        save_config(config); page.update()

    def update_panel_blur(e):
        config["panel_blur"] = int(e.control.value)
        details_panel.blur = ft.Blur(config["panel_blur"], config["panel_blur"], ft.BlurTileMode.MIRROR)
        save_config(config); page.update()

    def keyboard_handler(e: ft.KeyboardEvent):
        if e.key == "F11" or (e.key == "Enter" and e.ctrl): toggle_fullscreen()

    page.on_keyboard_event = keyboard_handler

    switch_fullscreen = ft.Switch(label="Plein écran", value=config.get("fullscreen", False), on_change=toggle_fullscreen)
    switch_big_picture = ft.Switch(label="Mode Big Picture (TV)", value=config.get("big_picture", False), on_change=toggle_big_picture)
    # ==========================================
    # GESTION DES DOSSIERS ET RÉSEAUX
    # ==========================================
    folders_list_view = ft.ListView(expand=True, spacing=5)
    storages_list_view = ft.ListView(expand=True, spacing=5)
    federated_list_view = ft.ListView(expand=True, spacing=5)
    discovery_tree_view = ft.Column(spacing=5)

    def add_direct_unc_path(path_to_add):
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO scanned_folders (folder_path) VALUES (?)", (path_to_add,))
            conn.commit(); refresh_settings_lists()
        except sqlite3.IntegrityError: pass
        finally: conn.close()

    def discover_network_action(e):
        discovery_tree_view.controls.clear()
        discovery_tree_view.controls.append(ft.ProgressRing(width=20, height=20))
        page.update()
        def run_discovery():
            shares_structure = scan_network_via_netview()
            discovery_tree_view.controls.clear()
            if not shares_structure:
                discovery_tree_view.controls.append(ft.Text("Aucun appareil détecté.", color="grey400", italic=True))
            else:
                for machine, shares in shares_structure.items():
                    sub_controls = [ft.Row([ft.Icon("folder_open", color="#ffc107", size=16), ft.Text(s), ft.TextButton("Ajouter", on_click=lambda e, p=f"\\\\{machine}\\{s}": add_direct_unc_path(p))]) for s in shares]
                    discovery_tree_view.controls.append(ft.ExpansionTile(title=ft.Text(machine, weight="bold", color="#E50914"), leading=ft.Icon("computer"), controls=sub_controls, initially_expanded=True))
            page.update()
        threading.Thread(target=run_discovery, daemon=True).start()

    def remove_folder(folder):
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        cur.execute("DELETE FROM scanned_folders WHERE folder_path=?", (folder,))
        conn.commit(); conn.close(); refresh_settings_lists()

    def remove_network_storage(st_id):
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        cur.execute("DELETE FROM network_storages WHERE id=?", (st_id,))
        conn.commit(); conn.close(); refresh_settings_lists()

    def remove_federated_server(srv_id):
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        cur.execute("DELETE FROM federated_servers WHERE id=?", (srv_id,))
        conn.commit(); conn.close(); refresh_settings_lists(); refresh_server_selector_options()

    def refresh_settings_lists():
        folders_list_view.controls.clear()
        for f in get_scanned_folders():
            folders_list_view.controls.append(ft.ListTile(leading=ft.Icon("folder", color="#E50914"), title=ft.Text(f), trailing=ft.IconButton(icon="delete", on_click=lambda e, p=f: remove_folder(p))))
        storages_list_view.controls.clear()
        for st in get_network_storages_full():
            storages_list_view.controls.append(ft.ListTile(leading=ft.Icon("nas"), title=ft.Text(f"{st[1]} [{st[2]}]"), subtitle=ft.Text(f"{st[3]}/{st[6]}"), trailing=ft.IconButton(icon="delete", on_click=lambda e, sid=st[0]: remove_network_storage(sid))))
        federated_list_view.controls.clear()
        for srv in get_federated_servers():
            federated_list_view.controls.append(ft.ListTile(leading=ft.Icon("dns"), title=ft.Text(srv[1]), subtitle=ft.Text(srv[2]), trailing=ft.IconButton(icon="delete", on_click=lambda e, sid=srv[0]: remove_federated_server(sid))))
        page.update()

    def add_folder_to_scan(e):
        file_picker = ft.FilePicker(on_result=lambda e: add_direct_unc_path(os.path.normpath(e.path)) if e.path else None)
        page.overlay.append(file_picker); page.update(); file_picker.get_directory_path()

    nas_name = ft.TextField(label="Nom de l'appareil", expand=True)
    nas_type = ft.Dropdown(label="Protocole", options=[ft.dropdown.Option("SMB"), ft.dropdown.Option("FTP"), ft.dropdown.Option("SFTP")], value="SMB", width=120)
    nas_host = ft.TextField(label="Adresse IP", expand=True)
    nas_user = ft.TextField(label="Utilisateur", expand=True)
    nas_pass = ft.TextField(label="Mot de passe", password=True, can_reveal_password=True, expand=True)
    nas_root = ft.TextField(label="Dossier partagé", expand=True)

    def submit_network_storage(e):
        if nas_name.value and nas_host.value and nas_root.value:
            conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
            try:
                cursor.execute("INSERT INTO network_storages (name, type, host, user, password, root_path) VALUES (?, ?, ?, ?, ?, ?)", (nas_name.value, nas_type.value, nas_host.value, nas_user.value, nas_pass.value, nas_root.value))
                conn.commit(); refresh_settings_lists()
                nas_name.value = ""; nas_host.value = ""; nas_user.value = ""; nas_pass.value = ""; nas_root.value = ""
            except sqlite3.IntegrityError: pass
            finally: conn.close()

    fed_name = ft.TextField(label="Nom du Cinéma Distant", expand=True)
    fed_url = ft.TextField(label="Adresse (ex: http://192.168.1.50:8080)", expand=True)

    def submit_federated_server(e):
        if fed_name.value and fed_url.value:
            conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
            try:
                cursor.execute("INSERT INTO federated_servers (name, url) VALUES (?, ?)", (fed_name.value, fed_url.value.strip().rstrip('/')))
                conn.commit(); refresh_settings_lists(); refresh_server_selector_options()
                fed_name.value = ""; fed_url.value = ""
            except sqlite3.IntegrityError: pass
            finally: conn.close()

    # ==========================================
    # PANNEAU DE DÉTAILS (OPTION B - Lecteur intégré)
    # ==========================================
    # Configuration du lecteur vidéo dédié avant son insertion
    if FLET_VIDEO_AVAILABLE:
        trailer_video_player.height = 220
        trailer_video_player.visible = False

    trailer_button = ft.ElevatedButton(
        "🎬 Bande-annonce", 
        on_click=lambda e: play_trailer(e), 
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))
    )
    
    # Appliquer l'opacité configurée au bouton trailer
    trailer_button.opacity = config.get("buttons_opacity", 1.0)

    details_panel = ft.Container(
        content=ft.Column([
            ft.Row([poster_image], alignment="center"), 
            title_text, rating_text, cast_text, synopsis_text, 
            
            # OPTION B : Composant vidéo fixe incrusté juste en dessous du résumé
            ft.Row([trailer_video_player], alignment="center"),
            
            ft.Divider(color="grey800"), 
            ft.Row([player_dropdown], alignment="center"), 
            ft.Row([play_button], alignment="center"), 
            ft.Divider(color="grey800"), 
            ft.Row([language_dropdown], alignment="center"), 
            ft.Row([subtitles_button], alignment="center"),
            ft.Row([trailer_button], alignment="center")
        ], spacing=15, scroll="auto"), 
        expand=1, 
        bgcolor=ft.Colors.with_opacity(config.get("details_opacity", 0.60), ft.Colors.BLACK),
        border_radius=15, 
        padding=25,
        blur=ft.Blur(config.get("panel_blur", 20), config.get("panel_blur", 20), ft.BlurTileMode.MIRROR),
        border=ft.border.all(1, ft.Colors.with_opacity(0.15, ft.Colors.WHITE))
    )

    library_view = ft.Row([
        ft.Column([
            ft.Row([
                ft.Text("Catalogue :", size=16, weight="bold"), 
                server_selector, 
                ft.ElevatedButton("Rafraîchir", on_click=lambda e: update_gallery(), style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))), 
                ft.ElevatedButton("Rechercher", on_click=scan_library, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))), 
                ft.ElevatedButton("Nettoyer", on_click=force_clean_library, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)))
            ], spacing=15),
            gallery_grid
        ], expand=2),
        details_panel
    ], expand=True)

    # ==========================================
    # CONFIGURATION SOUS-TITRES
    # ==========================================
    accs_cfg = config.get("subtitle_accounts", {})
    os_api_key_field    = ft.TextField(label="Clé API OpenSubtitles", value=accs_cfg.get("opensubtitles", {}).get("api_key", ""), expand=True, password=True, can_reveal_password=True)
    os_username_field   = ft.TextField(label="Identifiant", value=accs_cfg.get("opensubtitles", {}).get("username", ""), expand=True)
    os_password_field   = ft.TextField(label="Mot de passe", value=accs_cfg.get("opensubtitles", {}).get("password", ""), expand=True, password=True, can_reveal_password=True)

    def save_subtitle_service_accounts(e):
        config["subtitle_accounts"] = {
            "opensubtitles": {
                "api_key":  os_api_key_field.value.strip(),
                "username": os_username_field.value.strip(),
                "password": os_password_field.value.strip(),
            }
        }
        save_config(config)
        show_snackbar("✅ Comptes sous-titres sauvegardés.", "#4caf50")

    subtitle_accounts_panel = ft.Container(
        content=ft.Column([
            ft.Text("🔑 Connexion OpenSubtitles (optionnel mais recommandé)", size=15, weight="bold", color="white"),
            create_help_box(
                "Le nouveau moteur utilise le Hash vidéo, mais un compte gratuit augmente drastiquement vos quotas.",
                "Exemple : Clé API → abc123XYZ  |  Login → mon_pseudo  |  Mot de passe → ****"
            ),
            ft.Row([os_api_key_field, os_username_field, os_password_field]),
            ft.Row([
                ft.ElevatedButton("💾 Enregistrer", on_click=save_subtitle_service_accounts, style=ft.ButtonStyle(bgcolor={"": "#1565C0"}, color={"": "white"})),
                ft.TextButton("📝 Créer un compte", url="https://www.opensubtitles.com/fr/register", style=ft.ButtonStyle(color="grey400")),
                ft.TextButton("🔑 Obtenir une clé API", url="https://www.opensubtitles.com/fr/consumers", style=ft.ButtonStyle(color="grey400")),
            ])
        ], spacing=10),
        bgcolor=ft.Colors.with_opacity(0.12, "white"), border_radius=10, padding=16, border=ft.border.all(1, ft.Colors.with_opacity(0.15, "#1565C0"))
    )

    # ==========================================
    # SÉLECTEUR DE LECTEUR VIDÉO
    # ==========================================
    player_preference_dropdown = ft.Dropdown(
        label="Lecteur vidéo par défaut",
        options=[
            ft.dropdown.Option("external", "Lecteur externe (VLC, MPV...)"),
            ft.dropdown.Option("native", "Lecteur natif Flet")
        ],
        value=config.get("preferred_player", "external"),
        width=250,
        border_color="#E50914"
    )
    
    def update_player_preference(e):
        config["preferred_player"] = player_preference_dropdown.value
        save_config(config)
        show_snackbar(f"Lecteur par défaut: {player_preference_dropdown.value}")

    def auto_detect_players():
        """Détecte automatiquement les lecteurs vidéo installés sur Windows."""
        if not is_windows():
            return

        detected = {}
        common_paths = {
            "VLC": [
                "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe",
                "C:\\Program Files (x86)\\VideoLAN\\VLC\\vlc.exe",
                os.path.join(os.environ.get("PROGRAMFILES", ""), "VideoLAN\\VLC\\vlc.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "VideoLAN\\VLC\\vlc.exe")
            ],
            "MPV": [
                "C:\\Program Files\\mpv\\mpv.exe",
                "C:\\Program Files (x86)\\mpv\\mpv.exe",
                os.path.join(os.environ.get("PROGRAMFILES", ""), "mpv\\mpv.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "mpv\\mpv.exe")
            ],
            "PotPlayer": [
                "C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe",
                "C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini.exe",
                "C:\\Program Files (x86)\\DAUM\\PotPlayer\\PotPlayerMini64.exe",
                os.path.join(os.environ.get("PROGRAMFILES", ""), "DAUM\\PotPlayer\\PotPlayerMini64.exe")
            ],
            "MPC-BE": [
                "C:\\Program Files\\MPC-BE\\mpc-be64.exe",
                "C:\\Program Files\\MPC-BE\\mpc-be.exe",
                "C:\\Program Files (x86)\\MPC-BE\\mpc-be64.exe",
                os.path.join(os.environ.get("PROGRAMFILES", ""), "MPC-BE\\mpc-be64.exe")
            ],
            "MPC-HC": [
                "C:\\Program Files\\MPC-HC\\mpc-hc64.exe",
                "C:\\Program Files\\MPC-HC\\mpc-hc.exe",
                "C:\\Program Files (x86)\\MPC-HC\\mpc-hc64.exe",
                os.path.join(os.environ.get("PROGRAMFILES", ""), "MPC-HC\\mpc-hc64.exe")
            ]
        }

        for app_name, paths in common_paths.items():
            for path in paths:
                if os.path.exists(path):
                    detected[app_name] = path
                    break

        if detected:
            if "players" not in config:
                config["players"] = {}
            config["players"].update(detected)
            save_config(config)
            print(f"[OmniCine] Lecteurs détectés: {list(detected.keys())}")
            return detected
        return None

    def auto_detect_players_action(e):
        """Action de détection automatique depuis l'interface."""
        detected = auto_detect_players()
        if detected:
            show_snackbar(f"✅ Lecteurs détectés: {', '.join(detected.keys())}", "#4caf50")
            # Mettre à jour le dropdown player_dropdown
            if 'player_dropdown' in globals() and hasattr(player_dropdown, "options"):
                current_keys = {opt.key for opt in player_dropdown.options}
                for name in detected.keys():
                    if name not in current_keys:
                        player_dropdown.options.append(ft.dropdown.Option(name))
                if player_dropdown.options:
                    chosen_player = player_dropdown.options[0].key
                    player_dropdown.value = chosen_player
                    
                    # APPLICATION AUTOMATIQUE DIRECTE DANS LA CONFIGURATION
                    config["player"] = chosen_player
                    save_config(config)
                    
                page.update()
        else:
            show_snackbar("Aucun lecteur détecté")

    # ==========================================
    # SÉLECTEUR DE SOURCES DE SOUS-TITRES
    # ==========================================
    subtitle_sources_dropdown = ft.Dropdown(
        label="Ordre de priorité des sources",
        options=[
            ft.dropdown.Option("subtitlecat,opensubtitles", "SubtitleCat → OpenSubtitles"),
            ft.dropdown.Option("opensubtitles,subtitlecat", "OpenSubtitles → SubtitleCat"),
            ft.dropdown.Option("subtitlecat", "SubtitleCat uniquement"),
            ft.dropdown.Option("opensubtitles", "OpenSubtitles uniquement")
        ],
        value=",".join(config.get("subtitle_sources", ["subtitlecat", "opensubtitles"])),
        width=300,
        border_color="#E50914"
    )
    
    def update_subtitle_sources(e):
        sources = subtitle_sources_dropdown.value.split(",")
        config["subtitle_sources"] = sources
        save_config(config)
        show_snackbar("Ordre des sources mis à jour.")

    # ==========================================
    # AJOUT DE LECTEURS CUSTOM
    # ==========================================
    custom_player_name = ft.TextField(label="Nom du lecteur", expand=True)
    custom_player_path = ft.TextField(label="Chemin de l'application", expand=True, read_only=True)

    def on_player_file_selected(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            custom_player_path.value = os.path.normpath(e.files[0].path)
            page.update()

    player_file_picker = ft.FilePicker(on_result=on_player_file_selected)
    page.overlay.append(player_file_picker)

    def save_custom_player(e):
        name = custom_player_name.value.strip()
        path = custom_player_path.value.strip()
        if not name or not path or not os.path.exists(path):
            show_snackbar("Veuillez vérifier le nom et le chemin.")
            return
        config["players"][name] = path
        save_config(config)
        player_dropdown.options.append(ft.dropdown.Option(name))
        player_dropdown.value = name
        custom_player_name.value = ""; custom_player_path.value = ""
        page.update(); show_snackbar(f"Lecteur '{name}' ajouté !")

    # ==========================================
    # SYSTÈME DE MISE À JOUR
    # ==========================================
    update_status_text = ft.Text("Version actuelle: " + config.get("app_version", "1.0.0"), size=14, color="grey400")

    def check_updates_action(e):
        def run_check():
            # Correction de la coroutine
            update_available, version_info = asyncio.run(check_for_updates())
            if update_available:
                update_status_text.value = f"🎉 Nouvelle version disponible: {version_info['latest_version']}"
                update_status_text.color = "#4caf50"
                
                # Fonction interne qui gère le téléchargement automatique et sécurisé
                def lancer_maj_auto(e_click):
                    try:
                        update_status_text.value = "⏳ Téléchargement de la mise à jour en cours..."
                        update_status_text.color = "#ff9800"
                        page.close(update_dialog)
                        page.update()

                        # Détection automatique de l'environnement d'exécution
                        is_frozen = getattr(sys, 'frozen', False)
                        
                        if is_frozen:
                            # --- MODE APPLICATION STANDALONE WINDOWS (.EXE) ---
                            exe_path = sys.executable
                            exe_bak = exe_path + ".bak"
                            exe_new = exe_path + ".new"
                            
                            # 1. ÉTAPE DE SÉCURITÉ : On effectue une copie de secours du binaire actuel
                            if os.path.exists(exe_path):
                                shutil.copy(exe_path, exe_bak)
                            
                            # 2. TÉLÉCHARGEMENT : Récupération du nouvel exécutable complet
                            url_cible = version_info.get("exe_url") or version_info.get("download_url", "")
                            reponse = requests.get(url_cible, timeout=60)
                            
                            if reponse.status_code == 200:
                                # Enregistrement du flux binaire brut
                                with open(exe_new, "wb") as f:
                                    f.write(reponse.content)
                                
                                # Mettre à jour la version dans le fichier config local
                                config["app_version"] = version_info['latest_version']
                                save_config(config)
                                
                                update_status_text.value = "✅ Téléchargement réussi ! Redémarrage automatique..."
                                update_status_text.color = "#4caf50"
                                page.update()
                                time.sleep(1)
                                
                                # 3. SCRIPT INVISIBLE : Permutation physique à la fermeture
                                temp_dir = tempfile.gettempdir()
                                batch_path = os.path.join(temp_dir, "omnicine_updater.bat")
                                with open(batch_path, "w", encoding="utf-8") as f:
                                    f.write('@echo off\n')
                                    f.write('timeout /t 2 /nobreak > nul\n')
                                    f.write(f'move /y "{exe_new}" "{exe_path}"\n')
                                    f.write(f'start "" "{exe_path}"\n')
                                    f.write('del "%~f0"\n')
                                
                                # Lancement en tâche de fond detached et fermeture immédiate de l'ancienne version
                                subprocess.Popen([batch_path], shell=True)
                                sys.exit(0)
                            else:
                                raise Exception(f"Erreur de communication avec le serveur (Code {reponse.status_code})")
                        
                        else:
                            # --- MODE DE DÉVELOPPEMENT CLASSIQUE (.PY) ---
                            if os.path.exists("omnicine.py"):
                                shutil.copy("omnicine.py", "omnicine.py.bak")

                            reponse = requests.get(version_info.get("download_url", ""), timeout=15)
                            if reponse.status_code == 200:
                                with open("omnicine.py", "w", encoding="utf-8") as f:
                                    f.write(reponse.text)
                                
                                config["app_version"] = version_info['latest_version']
                                save_config(config)

                                update_status_text.value = "✅ Mise à jour installée ! Redémarrage..."
                                update_status_text.color = "#4caf50"
                                page.update()
                                time.sleep(2)

                                os.execv(sys.executable, [sys.executable] + sys.argv)
                            else:
                                raise Exception(f"Erreur GitHub (Code {reponse.status_code})")

                    except Exception as err:
                        update_status_text.value = f"❌ Échec de l'installation : {err}"
                        update_status_text.color = "#f44336"
                        # Restauration d'urgence automatique uniquement en mode script
                        if not is_frozen and os.path.exists("omnicine.py.bak") and not os.path.exists("omnicine.py"):
                            shutil.copy("omnicine.py.bak", "omnicine.py")
                        page.update()

                # Afficher dialogue de mise à jour
                update_dialog = ft.AlertDialog(
                    title=ft.Text("Mise à jour disponible", size=20, weight="bold", color="white"),
                    bgcolor="#1a1a2e",
                    content=ft.Column([
                        ft.Text(f"Version actuelle: {version_info.get('current_version', config.get('app_version', '1.0.0'))}", color="grey400"),
                        ft.Text(f"Nouvelle version: {version_info['latest_version']}", color="#4caf50", weight="bold"),
                        ft.Text(version_info.get("changelog", ""), color="grey300", max_lines=5, overflow="ellipsis"),
                        ft.ElevatedButton("Installer la mise à jour", on_click=lancer_maj_auto, style=ft.ButtonStyle(bgcolor={"": "#E50914"}, color={"": "white"}))
                    ], spacing=10),
                    actions=[ft.TextButton("Fermer", on_click=lambda e: page.close(update_dialog), style=ft.ButtonStyle(color="white"))]
                )
                page.open(update_dialog)
            else:
                update_status_text.value = "✅ Vous êtes à jour."
                update_status_text.color = "#4caf50"
            page.update()
        threading.Thread(target=run_check, daemon=True).start()

    def rollback_action(e):
        """Effectue un rollback vers la version précédente (.exe ou .py)."""
        try:
            is_frozen = getattr(sys, 'frozen', False)
            
            if is_frozen:
                # --- STRATÉGIE DE RESTAURATION EXÉCUTABLE ---
                exe_path = sys.executable
                exe_bak = exe_path + ".bak"
                
                if os.path.exists(exe_bak):
                    temp_dir = tempfile.gettempdir()
                    batch_path = os.path.join(temp_dir, "omnicine_rollback.bat")
                    with open(batch_path, "w", encoding="utf-8") as f:
                        f.write('@echo off\n')
                        f.write('timeout /t 2 /nobreak > nul\n')
                        f.write(f'move /y "{exe_bak}" "{exe_path}"\n')
                        f.write(f'start "" "{exe_path}"\n')
                        f.write('del "%~f0"\n')
                    
                    # Exécution furtive du script et fermeture instantanée du processus instable
                    subprocess.Popen([batch_path], shell=True)
                    sys.exit(0)
                else:
                    show_snackbar("❌ Aucun backup de l'application disponible.")
            
            else:
                # --- STRATÉGIE DE RESTAURATION LOGICIELLE EN DEV ---
                if os.path.exists("omnicine.py.bak"):
                    shutil.copy("omnicine.py.bak", "omnicine.py")
                    
                    # On restaure aussi la config
                    restore_config_backup()
                    
                    show_snackbar("✅ Application restaurée à la version précédente. Redémarrage...", "#4caf50")
                    page.update()
                    time.sleep(2)
                    
                    # Relancer l'application immédiatement sur l'ancienne version
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                else:
                    show_snackbar("❌ Aucun backup de l'application disponible.")
        except Exception as err:
            show_snackbar(f"❌ Erreur lors du rollback : {err}")

    # ==========================================
    # VUE DES RÉGLAGES
    # ==========================================
    settings_view = ft.Container(
        content=ft.Column([
            ft.Text("🛠️ Centre de Configuration OmniCine", size=30, weight="bold"),
            ft.Divider(), 
            
            ft.Text("⚙️ Personnalisation de l'application", size=18, weight="bold", color="#E50914"),
            ft.Row([app_name_input, tmdb_key_input, server_port_input]),
            ft.ElevatedButton("Sauvegarder", on_click=update_app_settings, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
            
            ft.Divider(),
            ft.Text("🎨 Affichage & Apparence", size=18, weight="bold", color="#E50914"),
            ft.Row([switch_fullscreen, switch_big_picture], spacing=30),
            ft.Column([
                ft.Text("Opacité du fond dynamique", size=14, color="grey300"), ft.Slider(min=0.0, max=1.0, value=config.get("ui_opacity", 0.55), on_change=update_backdrop_opacity),
                ft.Text("Opacité du panneau", size=14, color="grey300"), ft.Slider(min=0.0, max=1.0, value=config.get("details_opacity", 0.60), on_change=update_details_opacity),
                ft.Text("Opacité de la galerie", size=14, color="grey300"), ft.Slider(min=0.0, max=1.0, value=config.get("gallery_opacity", 1.0), on_change=update_gallery_opacity),
                ft.Text("Opacité des boutons", size=14, color="grey300"), ft.Slider(min=0.0, max=1.0, value=config.get("buttons_opacity", 1.0), on_change=update_buttons_opacity),
                ft.Text("Opacité du bandeau", size=14, color="grey300"), ft.Slider(min=0.0, max=1.0, value=config.get("banner_opacity", 0.78), on_change=update_banner_opacity),
                ft.Text("Flou du fond", size=14, color="grey300"), ft.Slider(min=0, max=100, value=config.get("background_blur", 40), on_change=update_background_blur),
                ft.Text("Flou du panneau", size=14, color="grey300"), ft.Slider(min=0, max=100, value=config.get("panel_blur", 20), on_change=update_panel_blur),
            ], spacing=8),
            
            ft.Divider(), 
            ft.Text("🎬 Lecteur vidéo", size=18, weight="bold", color="#E50914"),
            ft.Row([player_preference_dropdown]),
            ft.ElevatedButton("Appliquer", on_click=update_player_preference, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
            ft.ElevatedButton("🔍 Détecter automatiquement les lecteurs", on_click=auto_detect_players_action, style=ft.ButtonStyle(bgcolor={"": "#1565C0"}, color={"": "white"}, shape=ft.RoundedRectangleBorder(radius=8))),
            
            ft.Divider(),
            ft.Text("🔍 Recherche automatique d'ordinateurs", size=18, weight="bold"),
            ft.ElevatedButton("Lancer la recherche", on_click=discover_network_action, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
            ft.Container(content=discovery_tree_view, bgcolor=ft.Colors.with_opacity(0.4, "#1e1e1e"), border_radius=8, padding=12),
            
            ft.Divider(), 
            ft.Text("📁 Ajouter des films depuis ce PC", size=18, weight="bold"),
            ft.ElevatedButton("➕ Ajouter un dossier", on_click=add_folder_to_scan, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
            ft.Container(content=folders_list_view, height=120, bgcolor=ft.Colors.with_opacity(0.4, "#1e1e1e"), border_radius=8, padding=6),
            
            ft.Divider(), 
            ft.Text("🌐 Connecter un disque réseau / Box", size=18, weight="bold"),
            ft.Row([nas_name, nas_type, nas_host, nas_user, nas_pass, nas_root]),
            ft.ElevatedButton("Établir la connexion", on_click=submit_network_storage, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
            ft.Container(content=storages_list_view, height=140, bgcolor=ft.Colors.with_opacity(0.4, "#1e1e1e"), border_radius=8, padding=6),
            
            ft.Divider(), 
            ft.Text("🤝 Partager avec des amis", size=18, weight="bold"),
            ft.Row([fed_name, fed_url]),
            ft.ElevatedButton("Associer", on_click=submit_federated_server, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
            ft.Container(content=federated_list_view, height=140, bgcolor=ft.Colors.with_opacity(0.4, "#1e1e1e"), border_radius=8, padding=6),

            ft.Divider(),
            ft.Text("🎯 Services de sous-titres", size=18, weight="bold", color="#E50914"),
            subtitle_accounts_panel,
            ft.Row([subtitle_sources_dropdown]),
            ft.ElevatedButton("Appliquer l'ordre", on_click=update_subtitle_sources, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),

            ft.Divider(), 
            ft.Text("🚀 Ajouter un lecteur multimédia", size=18, weight="bold"),
            ft.Row([custom_player_name, custom_player_path, ft.IconButton(icon="folder_open", on_click=lambda e: player_file_picker.pick_files(allowed_extensions=["exe"] if is_windows() else []))]),
            ft.Row([
                ft.ElevatedButton("Enregistrer ce lecteur", on_click=save_custom_player, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
                delete_player_button
            ], spacing=10),

            ft.Divider(),
            ft.Text("🔄 Mises à jour", size=18, weight="bold", color="#E50914"),
            update_status_text,
            ft.Row([
                ft.ElevatedButton("Vérifier les mises à jour", on_click=check_updates_action, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
                ft.ElevatedButton("Rollback (Restaurer config)", on_click=rollback_action, style=ft.ButtonStyle(bgcolor={"": "#ffc107"}, color={"": "black"}, shape=ft.RoundedRectangleBorder(radius=8)))
            ], spacing=10)
        ], spacing=20, scroll="auto", expand=True),
        bgcolor=ft.Colors.with_opacity(0.6, "#141414"), padding=30, border_radius=15
    )

    # ==========================================
    # NAVIGATION
    # ==========================================
    content_switcher = ft.AnimatedSwitcher(
        content=library_view, transition=ft.AnimatedSwitcherTransition.FADE, duration=400,
        reverse_duration=200, switch_in_curve=ft.AnimationCurve.EASE_IN_OUT, switch_out_curve=ft.AnimationCurve.EASE_IN_OUT, expand=True
    )

    def switch_view(view_name):
        if view_name == "library":
            content_switcher.content = library_view
            btn_nav_library.bgcolor = "#E50914"; btn_nav_settings.bgcolor = ft.Colors.TRANSPARENT
            # On réaffiche la barre de genres si on retourne sur la bibliothèque
            genre_filter_container.visible = True
        else:
            content_switcher.content = settings_view
            btn_nav_library.bgcolor = ft.Colors.TRANSPARENT; btn_nav_settings.bgcolor = "#E50914"
            # On masque la barre de genres sur la page des réglages
            genre_filter_container.visible = False
        page.update()

    btn_nav_library = ft.ElevatedButton("🍿 MON CINÉMA", bgcolor="#E50914", color="white", on_click=lambda e: switch_view("library"), style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)), opacity=config.get("buttons_opacity", 1.0))
    btn_nav_settings = ft.ElevatedButton("⚙️ RÉGLAGES", bgcolor=ft.Colors.TRANSPARENT, color="white", on_click=lambda e: switch_view("settings"), style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)), opacity=config.get("buttons_opacity", 1.0))

    # 1. Déclaration de la barre de progression (Correction de l'icône en ft.Icons.REFRESH)
    progress_text = ft.Text("Initialisation...", color="white", size=13, weight="medium")
    progress_bar = ft.ProgressBar(value=0.0, color="#E50914", bgcolor="#1f1f1f", height=6, border_radius=3)
    progress_container = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.REFRESH, color="#E50914", size=16),
                progress_text
            ], spacing=8),
            progress_bar
        ], spacing=6),
        padding=12,
        bgcolor=ft.Colors.with_opacity(0.15, "black"),
        border_radius=8,
        visible=False
    )

    # ==========================================
    # LOGIQUE INTERNE DE FILTRAGE PAR CATEGORIES (GENRES & NATURE)
    # ==========================================
    genre_filter_container = ft.Container(
        padding=ft.padding.symmetric(horizontal=15, vertical=10),
        bgcolor=ft.Colors.with_opacity(0.78, "#141414"),  # Opacité à 78% : laisse passer l'arrière-plan sans altérer la lisibilité du texte
        border_radius=12,
        border=ft.border.all(1, ft.Colors.GREY_800)  # Fine délimitation pour la structure
    )

    def update_genre_filter_bar():
        """Génère dynamiquement les listes déroulantes de filtrage (Nature et Genres)."""
        if current_library_source == "local":
            videos = get_all_local_videos()
        else:
            try:
                resp = requests.get(f"{current_library_source}/library", timeout=5)
                videos = [(item["id"], item["title"], item["year"], item["filepath"], item["synopsis"], item["rating"], item["cast"], f"{current_library_source}/poster/{item['id']}", f"{current_library_source}/backdrop/{item['id']}", item["director"], item["genres"], item["runtime"], item["actors"], item["country"], None, None) for item in resp.json()] if resp.status_code == 200 else []
            except Exception:
                videos = []

        # Extraction unique des genres
        genres_set = set()
        for vid in videos:
            if len(vid) > 10 and vid[10]:
                for g in vid[10].split(","):
                    g_clean = g.strip()
                    if g_clean: genres_set.add(g_clean)
        all_genres = sorted(list(genres_set))

        # Lecture des filtres actuellement mémorisés dans la session flet
        active_genre = page.session.get("active_genre_filter") or "Tous"
        active_nature = page.session.get("gallery_filter") or "Tous"

        # Menu déroulant pour le type de média (Tous / Films / Séries)
        dropdown_nature = ft.Dropdown(
            value=active_nature,
            width=160,
            color="white",
            border_color=ft.Colors.GREY_700,
            focused_border_color="#E50914",
            options=[
                ft.dropdown.Option("Tous", "🍿 Tout Voir"),
                ft.dropdown.Option("Films", "🎬 Films"),
                ft.dropdown.Option("Séries", "📺 Séries")
            ],
            on_change=lambda e: [page.session.set("gallery_filter", e.control.value), update_gallery()]
        )

        # Construction dynamique des options de genres
        genre_options = [ft.dropdown.Option("Tous", "🎭 Catégories")]
        for genre in all_genres:
            genre_options.append(ft.dropdown.Option(genre, genre))

        # Menu déroulant pour les genres littéraux
        dropdown_genre = ft.Dropdown(
            value=active_genre,
            width=220,
            color="white",
            border_color=ft.Colors.GREY_700,
            focused_border_color="#E50914",
            options=genre_options,
            on_change=lambda e: [page.session.set("active_genre_filter", e.control.value), update_gallery()]
        )

        # Injection des menus déroulants dans le conteneur principal
        genre_filter_container.content = ft.Row(controls=[dropdown_nature, dropdown_genre], scroll=ft.ScrollMode.ADAPTIVE, spacing=15, wrap=False)
        genre_filter_container.update()

    # Redirection de la fonction d'origine pour injecter automatiquement la mise à jour de notre barre
    original_update_gallery = update_gallery
    def update_gallery_with_filters():
        original_update_gallery()
        try: update_genre_filter_bar()
        except Exception: pass
    update_gallery = update_gallery_with_filters

    # 2. Boutons de navigation et intégration de la barre dans main_ui
    btn_nav_library = ft.ElevatedButton("🍿 MON CINÉMA", bgcolor="#E50914", color="white", on_click=lambda e: switch_view("library"), style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)), opacity=config.get("buttons_opacity", 1.0))
    btn_nav_settings = ft.ElevatedButton("⚙️ RÉGLAGES", bgcolor=ft.Colors.TRANSPARENT, color="white", on_click=lambda e: switch_view("settings"), style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)), opacity=config.get("buttons_opacity", 1.0))

    main_ui = ft.Container(
        content=ft.Column([
            ft.Row([btn_nav_library, btn_nav_settings], spacing=15), 
            progress_container, 
            genre_filter_container, # Injection de notre ligne dynamique de filtres (Films, Séries, Action, Thriller...)
            ft.Divider(color="grey800"), 
            content_switcher
        ], expand=True), 
        padding=25, 
        expand=True
    )

    # 3. Gestion du double clic pour le plein écran
    last_right_click_time = [0.0]
    def handle_root_secondary_tap(e):
        current_time = time.time()
        if current_time - last_right_click_time[0] < 0.4:
            toggle_fullscreen(); last_right_click_time[0] = 0.0
        else: last_right_click_time[0] = current_time

    # 4. Ajout des éléments à la page
    page.add(
        ft.Stack([
            app_bg, ft.Container(bgcolor=ft.Colors.with_opacity(0.10, "#0c0c0c"), expand=True),
            ft.GestureDetector(
                on_secondary_tap=handle_root_secondary_tap, 
                on_double_tap=lambda e: toggle_fullscreen(), # Ajout du double-clic gauche pour le mode plein écran / fenêtré
                content=main_ui, 
                expand=True
            )
        ], expand=True)
    )

    # 5. Rafraîchissements initiaux de l'interface
    refresh_server_selector_options()
    apply_big_picture_dimensions()
    refresh_settings_lists()

    # Initialisation de l'affichage de notre nouvelle barre de filtres
    try: update_genre_filter_bar()
    except Exception: pass

    # 6. Lancement de la maintenance automatique en tâche de fond (Placé en toute fin)
    threading.Thread(target=run_maintenance_task, args=(True,), daemon=True).start()

if __name__ == "__main__":
    ft.app(target=main)