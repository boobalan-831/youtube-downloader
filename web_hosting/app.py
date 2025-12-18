from flask import Flask, render_template, request, jsonify, Response, send_file, stream_with_context
import yt_dlp
import os
import uuid
import time
import threading
import json
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Store session data
sessions = {}

# Ensure downloads directory exists
DOWNLOAD_FOLDER = os.path.join(os.getcwd(), 'downloads')
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# Ensure ffmpeg is executable if present in current dir
if os.path.exists('./ffmpeg'):
    os.chmod('./ffmpeg', 0o755)
    # Add current directory to PATH so yt-dlp can find it
    os.environ['PATH'] += os.pathsep + os.getcwd()

# Handle Cookies from Environment Variable (for Render/Cloud deployment)
if os.environ.get('YOUTUBE_COOKIES'):
    with open('cookies.txt', 'w') as f:
        f.write(os.environ['YOUTUBE_COOKIES'])
    logger.info("Loaded cookies.txt from environment variable")

@app.route('/')
def index():
    return render_template('index.html')

def extract_info_safe(url):
    """
    Robust extraction with Multi-Provider Fallback:
    1. yt-dlp (Direct with Stealth/Cookies)
    2. Cobalt API (External Service)
    3. Invidious API (Instance Rotation)
    """
    import requests
    
    # --- Attempt 1: Standard yt-dlp (Web/Android/iOS) ---
    cookies_path = os.path.join(os.getcwd(), 'cookies.txt')
    has_cookies = os.path.exists(cookies_path)
    
    clients = ['android', 'web', 'ios']
    if has_cookies:
        clients = ['web', 'android'] # Web is best with cookies

    for client in clients:
        try:
            logger.info(f"Attempt 1: yt-dlp with client {client}")
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'cachedir': False,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            }
            if has_cookies:
                ydl_opts['cookiefile'] = cookies_path
            
            if client == 'android':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['android']}}
            elif client == 'ios':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['ios']}}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info, ydl_opts
        except Exception as e:
            logger.warning(f"yt-dlp {client} failed: {e}")

    # --- Attempt 2: Cobalt API (Strong Backup) ---
    try:
        logger.info("Attempt 2: Cobalt API")
        # Cobalt is a powerful downloader API that handles the hard work
        cobalt_headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        cobalt_data = {
            'url': url,
            'vCodec': 'h264',
            'vQuality': '1080',
            'aFormat': 'mp3',
            'isAudioOnly': False
        }
        
        # We use a public instance or the main one
        resp = requests.post('https://api.cobalt.tools/api/json', headers=cobalt_headers, json=cobalt_data, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            if 'url' in data:
                # Map Cobalt response to our info format
                # Cobalt doesn't give full metadata always, but gives a working link
                info = {
                    'id': 'cobalt_video',
                    'title': 'YouTube Video (via Cobalt)',
                    'thumbnail': None,
                    'uploader': 'YouTube',
                    'duration': 0,
                    'view_count': 0,
                    'upload_date': None,
                    'formats': [{
                        'format_id': 'cobalt',
                        'url': data['url'],
                        'ext': 'mp4',
                        'vcodec': 'h264',
                        'acodec': 'mp3',
                        'height': 1080
                    }]
                }
                return info, {'fallback_source': 'cobalt', 'direct_url': data['url']}
    except Exception as e:
        logger.warning(f"Cobalt API failed: {e}")

    # --- Attempt 3: Invidious Instance Rotation ---
    invidious_instances = [
        "https://inv.tux.pizza",
        "https://vid.puffyan.us",
        "https://invidious.projectsegfau.lt",
        "https://invidious.fdn.fr"
    ]
    
    for instance in invidious_instances:
        try:
            logger.info(f"Attempt 3: Invidious API ({instance})")
            video_id = url.split('v=')[-1].split('&')[0]
            if 'youtu.be' in url:
                video_id = url.split('/')[-1]
                
            api_url = f"{instance}/api/v1/videos/{video_id}"
            resp = requests.get(api_url, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                formats = []
                for f in data.get('formatStreams', []) + data.get('adaptiveFormats', []):
                    formats.append({
                        'format_id': f.get('itag'),
                        'url': f.get('url'),
                        'ext': f.get('container'),
                        'vcodec': f.get('encoding'),
                        'acodec': f.get('audioEncoding'),
                        'height': f.get('resolution', '').replace('p', '') if f.get('resolution') else None
                    })
                
                info = {
                    'id': data['videoId'],
                    'title': data['title'],
                    'thumbnail': data['videoThumbnails'][0]['url'] if data.get('videoThumbnails') else None,
                    'uploader': data['author'],
                    'duration': data['lengthSeconds'],
                    'view_count': data['viewCount'],
                    'upload_date': data['publishedText'],
                    'formats': formats
                }
                return info, {'fallback_source': 'invidious'}
        except Exception as e:
            logger.warning(f"Invidious ({instance}) failed: {e}")

    raise Exception("All extraction methods failed. Server IP may be blocked by YouTube.")

@app.route('/get_info', methods=['POST'])
def get_info():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        info, _ = extract_info_safe(url)
        
        resolutions = []
        audio_formats = []
        
        formats = info.get('formats', [])
        seen_res = set()
        
        for f in formats:
            # Video formats
            # Invidious formats might have different keys, handled in mapping above
            h = f.get('height')
            if h and str(h).isdigit():
                if h not in seen_res:
                    resolutions.append({'value': f.get('format_id') or 'best', 'label': f'{h}p'})
                    seen_res.add(h)
        
        resolutions.sort(key=lambda x: int(x['label'][:-1]) if x['label'][:-1].isdigit() else 0, reverse=True)
        
        if not resolutions:
             resolutions.append({'value': 'best', 'label': 'Best Quality'})

        audio_formats.append({'value': 'bestaudio/best', 'label': 'Best Quality (MP3)'})

        return jsonify({
            'title': info.get('title'),
            'thumbnail': info.get('thumbnail'),
            'duration_formatted': _format_duration(info.get('duration')),
            'channel': info.get('uploader'),
            'viewCount': info.get('view_count'),
            'uploadDate': info.get('upload_date'),
            'resolutions': resolutions,
            'audio_formats': audio_formats
        })

    except Exception as e:
        logger.error(f"Error extracting info: {e}")
        return jsonify({'error': str(e)}), 500

def _format_duration(seconds):
    if not seconds: return "--:--"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{int(h)}:{int(m):02d}:{int(s):02d}"
    return f"{int(m)}:{int(s):02d}"

def download_worker(session_id, url, format_id, is_audio, subtitles=False):
    session = sessions[session_id]
    
    def progress_hook(d):
        if session['cancel_event'].is_set():
            raise yt_dlp.utils.DownloadError("Download cancelled")
            
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%','')
            try:
                session['progress'] = float(p)
            except:
                pass
            session['status'] = 'downloading'
            session['speed'] = d.get('_speed_str', '--')
            session['eta'] = d.get('_eta_str', '--')
            session['downloaded'] = d.get('_total_bytes_str') or d.get('_total_bytes_estimate_str') or '--'
            
        elif d['status'] == 'finished':
            session['status'] = 'processing'
            session['progress'] = 99
            session['temp_filename'] = d['filename']

    # Try standard download first
    try:
        # Check for cookies
        cookies_path = os.path.join(os.getcwd(), 'cookies.txt')
        has_cookies = os.path.exists(cookies_path)
        
        ydl_opts = {
            'format': format_id if not is_audio else 'bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'cachedir': False,
            # Start with standard Web client
            'extractor_args': {'youtube': {'player_client': ['web', 'android']}}, 
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
        
        if has_cookies:
            ydl_opts['cookiefile'] = cookies_path
            
        if is_audio:
             ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        
        if subtitles:
            ydl_opts['writesubtitles'] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            session['title'] = info.get('title', 'Video')
            filename = ydl.prepare_filename(info)
            if is_audio:
                base = os.path.splitext(filename)[0]
                filename = base + ".mp3"
            session['filename'] = os.path.basename(filename)
            session['file_path'] = filename
            session['status'] = 'complete'
            session['progress'] = 100
            sz = os.path.getsize(filename)
            session['filesize'] = f"{sz / (1024*1024):.2f} MiB"
            return # Success!

    except Exception as e:
        logger.warning(f"Standard download failed, trying fallbacks: {e}")
        
        # Check if we have a direct Cobalt URL ready
        # We need to retrieve the info/opts passed or re-run extract_info_safe?
        # Since download_worker is separate, we re-run logic mostly or rely on what we can do.
        # But actually, download_worker is called AFTER get_info.
        # Ideally, we should pass the 'fallback_source' to download_worker.
        # But here we will just implement the same fallback logic: Try Cobalt, then Invidious.
        
        import requests
        
        # --- Fallback 1: Cobalt API ---
        try:
            logger.info("Fallback 1: Cobalt API")
            cobalt_headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            cobalt_data = {
                'url': url,
                'vCodec': 'h264',
                'vQuality': '1080',
                'aFormat': 'mp3',
                'isAudioOnly': is_audio
            }
            resp = requests.post('https://api.cobalt.tools/api/json', headers=cobalt_headers, json=cobalt_data, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if 'url' in data:
                    target_url = data['url']
                    session['title'] = 'YouTube Video (via Cobalt)'
                    
                    filename = f"video_download.{'mp3' if is_audio else 'mp4'}"
                    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                    session['filename'] = filename
                    session['file_path'] = filepath
                    
                    with requests.get(target_url, stream=True) as r:
                        r.raise_for_status()
                        total_length = int(r.headers.get('content-length', 0))
                        dl = 0
                        with open(filepath, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                if session['cancel_event'].is_set(): raise Exception("Cancelled")
                                if chunk: 
                                    f.write(chunk)
                                    dl += len(chunk)
                                    if total_length > 0:
                                        session['progress'] = (dl / total_length) * 100
                                        session['downloaded'] = f"{dl / (1024*1024):.1f} MiB"
                    
                    session['status'] = 'complete'
                    session['progress'] = 100
                    session['filesize'] = f"{os.path.getsize(filepath) / (1024*1024):.2f} MiB"
                    return
        except Exception as ec:
             logger.warning(f"Cobalt fallback failed: {ec}")

        # --- Fallback 2: Invidious Rotation ---
        try:
            logger.info("Fallback 2: Invidious Rotation")
            invidious_instances = [
                "https://inv.tux.pizza",
                "https://vid.puffyan.us",
                "https://invidious.projectsegfau.lt",
                "https://invidious.fdn.fr"
            ]
            
            target_url = None
            
            # Find a working instance
            for instance in invidious_instances:
                try:
                    video_id = url.split('v=')[-1].split('&')[0]
                    if 'youtu.be' in url:
                        video_id = url.split('/')[-1]
                        
                    api_url = f"{instance}/api/v1/videos/{video_id}"
                    resp = requests.get(api_url, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        session['title'] = data['title']
                        
                        if is_audio:
                             audio_streams = [f for f in data.get('adaptiveFormats', []) if 'audio' in f.get('type', '')]
                             if audio_streams: target_url = audio_streams[-1]['url']
                        else:
                             streams = data.get('formatStreams', [])
                             if not streams: streams = data.get('adaptiveFormats', [])
                             if streams: target_url = streams[0]['url']
                        
                        if target_url: break
                except:
                    continue

            if not target_url:
                raise Exception("No download URL found in any Invidious instance")
                
            # Download using requests
            filename = f"{session.get('title', 'video')[:50]}.{'mp3' if is_audio else 'mp4'}"
            filename = "".join([c for c in filename if c.isalpha() or c.isdigit() or c in ' .-_']).strip()
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            
            session['filename'] = filename
            session['file_path'] = filepath
            
            with requests.get(target_url, stream=True) as r:
                r.raise_for_status()
                total_length = int(r.headers.get('content-length', 0))
                dl = 0
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if session['cancel_event'].is_set():
                             raise Exception("Cancelled")
                        if chunk: 
                            f.write(chunk)
                            dl += len(chunk)
                            if total_length > 0:
                                session['progress'] = (dl / total_length) * 100
                                session['downloaded'] = f"{dl / (1024*1024):.1f} MiB"
                                
            session['status'] = 'complete'
            session['progress'] = 100
            session['filesize'] = f"{os.path.getsize(filepath) / (1024*1024):.2f} MiB"
            
        except Exception as e2:
            logger.error(f"Invidious download failed: {e2}")
            session['status'] = 'error'
            session['error'] = f"All methods failed. Server: {str(e)} | Proxy: {str(e2)}"

@app.route('/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    resolution = data.get('resolution') 
    subtitles = data.get('subtitles', False)
    
    is_audio = 'audio' in str(resolution) or 'bestaudio' in str(resolution)
    
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        'status': 'starting',
        'progress': 0,
        'cancel_event': threading.Event(),
        'url': url
    }
    
    thread = threading.Thread(target=download_worker, args=(session_id, url, resolution, is_audio, subtitles))
    thread.start()
    
    return jsonify({'session_id': session_id})

@app.route('/progress/<session_id>')
def progress(session_id):
    def generate():
        while True:
            session = sessions.get(session_id)
            if not session:
                yield f"data: {json.dumps({'error': 'Session not found'})}\n\n"
                break
            
            data = {
                'status': session['status'],
                'progress': session.get('progress', 0),
                'speed': session.get('speed'),
                'eta': session.get('eta'),
                'downloaded': session.get('downloaded'),
                'title': session.get('title'),
                'filename': session.get('filename'),
                'filesize': session.get('filesize'),
                'error': session.get('error')
            }
            
            yield f"data: {json.dumps(data)}\n\n"
            
            if session['status'] in ['complete', 'error', 'cancelled']:
                break
            
            time.sleep(1)
            
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/cancel/<session_id>', methods=['POST'])
def cancel(session_id):
    session = sessions.get(session_id)
    if session:
        session['cancel_event'].set()
    return jsonify({'status': 'ok'})

@app.route('/serve/<session_id>')
def serve(session_id):
    session = sessions.get(session_id)
    if not session or not session.get('file_path') or not os.path.exists(session['file_path']):
        return "File not found or expired", 404
    
    return send_file(session['file_path'], as_attachment=True, download_name=session['filename'])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
