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

def extract_info_safe(url, custom_cookies=None):
    """
    Robust extraction using the 'ytsearch' trick and optional user-provided cookies.
    This avoids direct third-party APIs as per user request.
    """
    # Extract Video ID for the Search Trick
    video_id = url
    if 'v=' in url:
        video_id = url.split('v=')[-1].split('&')[0]
    elif 'youtu.be' in url:
        video_id = url.split('/')[-1]
    
    # Use Search URL instead of Direct URL (Bypass Trick)
    # Searching for the ID often lands on a different API endpoint
    search_query = f"ytsearch1:{video_id}"
    
    # Handle Cookies
    cookie_file = None
    if custom_cookies:
        cookie_file = os.path.join(DOWNLOAD_FOLDER, f"cookies_{uuid.uuid4()}.txt")
        with open(cookie_file, 'w') as f:
            f.write(custom_cookies)
    elif os.path.exists(os.path.join(os.getcwd(), 'cookies.txt')):
        cookie_file = os.path.join(os.getcwd(), 'cookies.txt')

    try:
        # We try the 'android' client first as it's the most robust native client
        logger.info("Attempting extraction via Search API (Android Client)")
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'cachedir': False,
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
        
        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Note: extract_info with ytsearch returns a playlist-like object
            info = ydl.extract_info(search_query, download=False)
            
            # Extract the first result
            if 'entries' in info:
                info = info['entries'][0]
                
            return info, ydl_opts, cookie_file

    except Exception as e:
        logger.error(f"Search Extraction failed: {e}")
        # Cleanup temp cookie if we created it
        if cookie_file and 'cookies_' in cookie_file and os.path.exists(cookie_file):
            try: os.remove(cookie_file)
            except: pass
        raise e

@app.route('/get_info', methods=['POST'])
def get_info():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        # We don't accept custom cookies in get_info yet to keep UI simple, 
        # but we could add it if the user wants "preview" to work on restricted videos.
        info, _, _ = extract_info_safe(url)
        
        resolutions = []
        audio_formats = []
        
        formats = info.get('formats', [])
        seen_res = set()
        
        for f in formats:
            # Video formats
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

def download_worker(session_id, url, format_id, is_audio, subtitles=False, cookies=None):
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

    try:
        # Re-run safe extraction to get the best client/opts for download
        # This is robust because it handles the search trick and cookies
        info, ydl_opts, temp_cookie_path = extract_info_safe(url, cookies)
        
        # Merge our download-specific opts
        ydl_opts.update({
            'format': format_id if not is_audio else 'bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'noplaylist': True,
        })
        
        if is_audio:
             ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        
        if subtitles:
            ydl_opts['writesubtitles'] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # We must use the info dict we already got, OR search again.
            # Using search again is safer for the downloader to resolve the stream.
            video_id = url
            if 'v=' in url: video_id = url.split('v=')[-1].split('&')[0]
            elif 'youtu.be' in url: video_id = url.split('/')[-1]
            search_query = f"ytsearch1:{video_id}"
            
            info = ydl.extract_info(search_query, download=True)
            # Info is now the playlist result, get first entry
            if 'entries' in info:
                info = info['entries'][0]

            filename = ydl.prepare_filename(info)
            if is_audio:
                base = os.path.splitext(filename)[0]
                filename = base + ".mp3"
            
            session['filename'] = os.path.basename(filename)
            session['file_path'] = filename
            
            if os.path.exists(filename):
                session['status'] = 'complete'
                session['progress'] = 100
                sz = os.path.getsize(filename)
                session['filesize'] = f"{sz / (1024*1024):.2f} MiB"
            else:
                raise Exception("File not found after download")
                
        # Cleanup
        if temp_cookie_path and 'cookies_' in temp_cookie_path and os.path.exists(temp_cookie_path):
            try: os.remove(temp_cookie_path)
            except: pass

    except Exception as e:
        if "Download cancelled" in str(e):
            session['status'] = 'cancelled'
        else:
            logger.error(f"Download error: {e}")
            session['status'] = 'error'
            session['error'] = str(e)

@app.route('/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    resolution = data.get('resolution') 
    subtitles = data.get('subtitles', False)
    cookies = data.get('cookies') # New field
    
    is_audio = 'audio' in str(resolution) or 'bestaudio' in str(resolution)
    
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        'status': 'starting',
        'progress': 0,
        'cancel_event': threading.Event(),
        'url': url
    }
    
    thread = threading.Thread(target=download_worker, args=(session_id, url, resolution, is_audio, subtitles, cookies))
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