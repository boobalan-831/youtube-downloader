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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_info', methods=['POST'])
def get_info():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
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

    ydl_opts = {
        'format': format_id if not is_audio else 'bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s'),
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
    }
    
    if is_audio:
         ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    
    if subtitles:
        ydl_opts['writesubtitles'] = True
        # ydl_opts['subtitleslangs'] = ['en'] # Could be parameterized

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            session['title'] = info.get('title', 'Video')
            
        # Determine final filename
        # Start with the template output
        final_path = None
        # Naive check: look for the file in the download folder that matches the title
        # This is tricky because of sanitization.
        
        # Helper to find the most recently modified file in download folder might be safer for this single-user-per-session context
        # but race conditions exist.
        
        # Better: use prepare_filename from ydl, but we need the 'info' dict after download/extraction.
        # 'info' variable above contains it.
        
        filename = ydl.prepare_filename(info)
        
        if is_audio:
            # It likely changed extension to .mp3
            base = os.path.splitext(filename)[0]
            filename = base + ".mp3"
            
        session['file_path'] = filename
        session['filename'] = os.path.basename(filename)
        
        if os.path.exists(filename):
            session['status'] = 'complete'
            session['progress'] = 100
            sz = os.path.getsize(filename)
            session['filesize'] = f"{sz / (1024*1024):.2f} MiB"
        else:
            session['status'] = 'error'
            session['error'] = 'File not found after download'

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
