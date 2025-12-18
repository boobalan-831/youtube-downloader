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
    Tries to extract video info using multiple client configurations.
    Returns the info dict or raises an exception if all attempts fail.
    """
    # Order of attempts: 
    # 1. 'android' (Often bypasses bot checks)
    # 2. 'web' (Standard, best metadata, but high bot detection)
    # 3. 'ios' (Backup mobile client)
    # 4. 'tv' (Android TV, sometimes restricted formats but different API)
    
    clients = ['android', 'web', 'ios', 'tv']
    last_error = None
    
    cookies_path = os.path.join(os.getcwd(), 'cookies.txt')
    has_cookies = os.path.exists(cookies_path)
    
    # If cookies are present, prioritize 'web' as it works best with auth
    if has_cookies:
        clients = ['web', 'android', 'ios']

    for client in clients:
        try:
            logger.info(f"Attempting extraction with client: {client}")
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            }
            
            if has_cookies:
                ydl_opts['cookiefile'] = cookies_path
            
            # Configure client-specific args
            if client == 'android':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['android', 'web']}}
            elif client == 'ios':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['ios']}}
            elif client == 'tv':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['android_tv']}}
            else: # web
                 ydl_opts['extractor_args'] = {'youtube': {'player_client': ['web']}}

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info, ydl_opts # Return successful opts to reuse for download
                
        except Exception as e:
            logger.warning(f"Failed with client {client}: {e}")
            last_error = e
            time.sleep(0.5) # Slight delay between attempts
            
    raise last_error

@app.route('/get_info', methods=['POST'])
def get_info():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        info, successful_opts = extract_info_safe(url)
        
        # Store successful options for this video/session if possible, 
        # or we just re-run the logic in download_worker (less efficient but stateless)
        # For now, we'll re-run logic in download_worker or pass the 'client' param.
        
        resolutions = []
        audio_formats = []
        
        formats = info.get('formats', [])
        seen_res = set()
        
        # Sort formats to find best ones
        for f in formats:
            # Video formats
            if f.get('vcodec') != 'none' and f.get('height'):
                h = f['height']
                if h not in seen_res:
                    resolutions.append({'value': f['format_id'], 'label': f'{h}p'})
                    seen_res.add(h)
        
        # Sort resolutions high to low
        resolutions.sort(key=lambda x: int(x['label'][:-1]) if x['label'][:-1].isdigit() else 0, reverse=True)
        
        # Add a "Best" option
        if not resolutions:
             resolutions.append({'value': 'best', 'label': 'Best Quality'})

        audio_formats.append({'value': 'bestaudio/best', 'label': 'Best Quality (MP3)'})

        return jsonify({
            'title': info.get('title'),
            'thumbnail': info.get('thumbnail'),
            'duration_formatted': info.get('duration_string') or _format_duration(info.get('duration')),
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

    # Retry Strategy for Download
    clients = ['android', 'web', 'ios', 'tv']
    cookies_path = os.path.join(os.getcwd(), 'cookies.txt')
    has_cookies = os.path.exists(cookies_path)
    
    if has_cookies:
        clients = ['web', 'android', 'ios']
        
    success = False
    last_error = None

    for client in clients:
        if success: break
        
        try:
            logger.info(f"Starting download with client: {client}")
            
            ydl_opts = {
                'format': format_id if not is_audio else 'bestaudio/best',
                'outtmpl': os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s'),
                'progress_hooks': [progress_hook],
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'nocheckcertificate': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            }
            
            if has_cookies:
                ydl_opts['cookiefile'] = cookies_path

            # Configure client-specific args
            if client == 'android':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['android', 'web']}}
            elif client == 'ios':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['ios']}}
            elif client == 'tv':
                ydl_opts['extractor_args'] = {'youtube': {'player_client': ['android_tv']}}
            else: # web
                 ydl_opts['extractor_args'] = {'youtube': {'player_client': ['web']}}
            
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
                
                # If we got here, it worked!
                filename = ydl.prepare_filename(info)
                if is_audio:
                    base = os.path.splitext(filename)[0]
                    filename = base + ".mp3"
                    
                session['file_path'] = filename
                session['filename'] = os.path.basename(filename)
                
                if os.path.exists(filename):
                    session['status'] = 'complete'
                    session['progress'] = 100
                    sz = os.path.getsize(filename)
                    session['filesize'] = f"{sz / (1024*1024):.2f} MiB"
                    success = True
                else:
                    raise Exception("File not found after download")

        except Exception as e:
            if "Download cancelled" in str(e):
                session['status'] = 'cancelled'
                return
            
            logger.warning(f"Download failed with client {client}: {e}")
            last_error = e
            # reset progress for next attempt
            session['progress'] = 0
            time.sleep(1)

    if not success:
        session['status'] = 'error'
        session['error'] = str(last_error)

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
