"""
YouTube Video Downloader - Enhanced Flask Backend
==================================================
A local web application for downloading YouTube videos in high quality.

Features:
- Download videos in multiple resolutions (144p to 4K/8K)
- Audio-only extraction (MP3/M4A/FLAC/WAV)
- Subtitle/caption download
- Thumbnail download
- Playlist support
- Download history
- Multiple concurrent downloads
- Detailed video information

IMPORTANT: Make sure ffmpeg is installed on your system and available in PATH.
- Windows: Download from https://ffmpeg.org/download.html and add to PATH
- Or use: choco install ffmpeg (if using Chocolatey)
- Or use: winget install ffmpeg
"""

import os
import re
import json
import threading
import tkinter as tk
from tkinter import filedialog
from flask import Flask, render_template, request, jsonify, Response
import yt_dlp
import queue
import time
from datetime import datetime
import urllib.request
import shutil

app = Flask(__name__)

# Path to ffmpeg - uses local copy if available, otherwise assumes it's in PATH
APP_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_PATH = os.path.join(APP_DIR, 'ffmpeg.exe') if os.path.exists(os.path.join(APP_DIR, 'ffmpeg.exe')) else 'ffmpeg'

# Global dictionaries for tracking
download_progress = {}
progress_queues = {}
download_history = []  # Stores completed downloads
active_downloads = {}  # Tracks active download threads

# Settings
MAX_HISTORY = 50  # Maximum number of history items to keep


def sanitize_filename(filename):
    """Remove invalid characters from filename."""
    return re.sub(r'[<>:"/\\|?*]', '', filename)


def format_filesize(bytes_size):
    """Convert bytes to human readable format."""
    if bytes_size is None:
        return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.2f} TB"


def format_duration(seconds):
    """Convert seconds to HH:MM:SS format."""
    if not seconds:
        return "00:00"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@app.route('/')
def index():
    """Serve the main page."""
    return render_template('index.html')


@app.route('/get_info', methods=['POST'])
def get_info():
    """
    Extract video information and available formats from a YouTube URL.
    Returns comprehensive video details including formats, subtitles, etc.
    """
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'Please provide a valid URL'}), 400
    
    # Validate URL format (basic check for YouTube)
    if not ('youtube.com' in url or 'youtu.be' in url):
        return jsonify({'error': 'Please provide a valid YouTube URL'}), 400
    
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Check if it's a playlist
            is_playlist = info.get('_type') == 'playlist'
            
            if is_playlist:
                # Handle playlist
                entries = info.get('entries', [])
                playlist_info = {
                    'is_playlist': True,
                    'playlist_title': info.get('title', 'Unknown Playlist'),
                    'playlist_count': len(entries),
                    'playlist_id': info.get('id', ''),
                    'videos': []
                }
                
                for entry in entries[:20]:  # Limit to first 20 for preview
                    if entry:
                        playlist_info['videos'].append({
                            'title': entry.get('title', 'Unknown'),
                            'url': entry.get('url', ''),
                            'duration': entry.get('duration', 0),
                            'thumbnail': entry.get('thumbnail', '')
                        })
                
                return jsonify({
                    'success': True,
                    **playlist_info
                })
            
            # Single video
            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail', '')
            duration = info.get('duration', 0)
            channel = info.get('channel', info.get('uploader', 'Unknown'))
            view_count = info.get('view_count', 0)
            upload_date = info.get('upload_date', '')
            description = info.get('description', '')[:500] if info.get('description') else ''
            
            # Extract available formats and resolutions
            formats = info.get('formats', [])
            resolutions = {}
            
            for fmt in formats:
                height = fmt.get('height')
                ext = fmt.get('ext', '')
                vcodec = fmt.get('vcodec', 'none')
                filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                tbr = fmt.get('tbr', 0)  # Total bitrate
                
                # Video formats
                if height and height >= 144 and vcodec != 'none':
                    if height not in resolutions or (tbr and tbr > resolutions[height].get('tbr', 0)):
                        resolutions[height] = {
                            'height': height,
                            'ext': ext,
                            'vcodec': vcodec,
                            'filesize': filesize,
                            'tbr': tbr
                        }
            
            # Sort resolutions in descending order
            sorted_resolutions = sorted(resolutions.keys(), reverse=True)
            
            # Create resolution options with friendly labels and file size estimates
            resolution_options = []
            for res in sorted_resolutions:
                res_info = resolutions[res]
                if res >= 2160:
                    label = f'4K ({res}p)'
                elif res >= 1440:
                    label = f'2K ({res}p)'
                elif res >= 1080:
                    label = f'Full HD ({res}p)'
                elif res >= 720:
                    label = f'HD ({res}p)'
                else:
                    label = f'{res}p'
                
                # Add estimated file size if available
                if res_info.get('filesize'):
                    label += f' • ~{format_filesize(res_info["filesize"])}'
                
                resolution_options.append({
                    'value': res,
                    'label': label,
                    'codec': res_info.get('vcodec', ''),
                    'filesize': res_info.get('filesize')
                })
            
            # Audio format options
            audio_options = [
                {'value': 'mp3-320', 'label': 'MP3 (320 kbps) - Best Quality'},
                {'value': 'mp3-192', 'label': 'MP3 (192 kbps) - Standard'},
                {'value': 'mp3-128', 'label': 'MP3 (128 kbps) - Smaller Size'},
                {'value': 'm4a', 'label': 'M4A (AAC) - Apple Compatible'},
                {'value': 'flac', 'label': 'FLAC - Lossless'},
                {'value': 'wav', 'label': 'WAV - Uncompressed'},
            ]
            
            # Get available subtitles
            subtitles = info.get('subtitles', {})
            auto_captions = info.get('automatic_captions', {})
            
            subtitle_options = []
            seen_langs = set()
            
            # Manual subtitles first
            for lang in subtitles.keys():
                if lang not in seen_langs:
                    seen_langs.add(lang)
                    subtitle_options.append({
                        'value': lang,
                        'label': f'{lang.upper()} (Manual)',
                        'auto': False
                    })
            
            # Then auto-generated (limit to common languages)
            common_langs = ['en', 'es', 'fr', 'de', 'pt', 'it', 'ru', 'ja', 'ko', 'zh', 'ar', 'hi']
            for lang in auto_captions.keys():
                if lang in common_langs and lang not in seen_langs:
                    seen_langs.add(lang)
                    subtitle_options.append({
                        'value': f'auto-{lang}',
                        'label': f'{lang.upper()} (Auto-generated)',
                        'auto': True
                    })
            
            # Format upload date
            formatted_date = ''
            if upload_date:
                try:
                    date_obj = datetime.strptime(upload_date, '%Y%m%d')
                    formatted_date = date_obj.strftime('%B %d, %Y')
                except:
                    formatted_date = upload_date
            
            return jsonify({
                'success': True,
                'is_playlist': False,
                'video_id': info.get('id', ''),
                'title': title,
                'thumbnail': thumbnail,
                'duration': duration,
                'duration_formatted': format_duration(duration),
                'channel': channel,
                'view_count': view_count,
                'view_count_formatted': f"{view_count:,}" if view_count else "0",
                'upload_date': formatted_date,
                'description': description,
                'resolutions': resolution_options,
                'audio_formats': audio_options,
                'subtitles': subtitle_options,
                'has_subtitles': len(subtitle_options) > 0
            })
            
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if 'Video unavailable' in error_msg:
            return jsonify({'error': 'This video is unavailable or private'}), 400
        elif 'age' in error_msg.lower():
            return jsonify({'error': 'This video is age-restricted'}), 400
        elif 'rate-limit' in error_msg.lower() or 'try again later' in error_msg.lower():
            return jsonify({'error': 'YouTube rate limit reached. Please wait a few minutes and try again.'}), 400
        return jsonify({'error': f'Failed to fetch video info: {error_msg}'}), 400
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500


@app.route('/select_folder', methods=['POST'])
def select_folder():
    """
    Open a native folder selection dialog using tkinter.
    Returns the selected folder path.
    """
    try:
        # Create a hidden tkinter root window
        root = tk.Tk()
        root.withdraw()  # Hide the main window
        root.attributes('-topmost', True)  # Bring dialog to front
        root.focus_force()  # Force focus
        
        # Open folder selection dialog
        folder_path = filedialog.askdirectory(
            title='Select Download Folder',
            initialdir=os.path.expanduser('~\\Downloads')
        )
        
        root.destroy()
        
        if folder_path:
            # Normalize path for Windows
            folder_path = os.path.normpath(folder_path)
            return jsonify({
                'success': True,
                'path': folder_path
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No folder selected'
            })
            
    except Exception as e:
        return jsonify({'error': f'Failed to open folder dialog: {str(e)}'}), 500


@app.route('/download', methods=['POST'])
def download():
    """
    Start the video download process.
    Runs in a separate thread to avoid blocking the server.
    """
    data = request.get_json()
    url = data.get('url', '').strip()
    resolution = data.get('resolution')
    save_path = data.get('save_path', '').strip()
    download_subtitles = data.get('subtitles', False)
    subtitle_lang = data.get('subtitle_lang', 'en')
    download_thumbnail = data.get('thumbnail', False)
    
    # Validation
    if not url:
        return jsonify({'error': 'Please provide a valid URL'}), 400
    
    if not resolution:
        return jsonify({'error': 'Please select a resolution or audio format'}), 400
    
    if not save_path:
        return jsonify({'error': 'Please select a save location'}), 400
    
    if not os.path.isdir(save_path):
        return jsonify({'error': 'Invalid save path'}), 400
    
    # Generate a unique session ID for this download
    session_id = f"{int(time.time() * 1000)}"
    download_progress[session_id] = {
        'status': 'starting',
        'progress': 0,
        'speed': '',
        'eta': '',
        'filename': '',
        'filesize': '',
        'downloaded': '',
        'error': None,
        'complete': False,
        'title': '',
        'thumbnail': ''
    }
    progress_queues[session_id] = queue.Queue()
    
    # Start download in a separate thread
    thread = threading.Thread(
        target=download_video,
        args=(url, resolution, save_path, session_id, 
              download_subtitles, subtitle_lang, download_thumbnail)
    )
    thread.daemon = True
    thread.start()
    active_downloads[session_id] = thread
    
    return jsonify({
        'success': True,
        'session_id': session_id,
        'message': 'Download started'
    })


@app.route('/cancel/<session_id>', methods=['POST'])
def cancel_download(session_id):
    """Cancel an active download."""
    if session_id in download_progress:
        download_progress[session_id]['status'] = 'cancelled'
        download_progress[session_id]['complete'] = True
        return jsonify({'success': True, 'message': 'Download cancelled'})
    return jsonify({'error': 'Download not found'}), 404


def download_video(url, resolution, save_path, session_id,
                   download_subtitles=False, subtitle_lang='en', download_thumbnail=False):
    """
    Download the video using yt-dlp with enhanced options.
    Updates progress in the global dictionary.
    """
    last_update_time = 0
    
    def progress_hook(d):
        """Callback function for download progress updates."""
        nonlocal last_update_time
        
        # Check if cancelled
        if download_progress.get(session_id, {}).get('status') == 'cancelled':
            raise Exception('Download cancelled by user')
        
        if d['status'] == 'downloading':
            # Throttle updates to every 50ms for performance
            current_time = time.time()
            if current_time - last_update_time < 0.05:
                return
            last_update_time = current_time
            
            # Extract progress information
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            
            if total > 0:
                percent = (downloaded / total) * 100
            else:
                # For fragmented downloads, try fragment progress
                fragment_index = d.get('fragment_index', 0)
                fragment_count = d.get('fragment_count', 0)
                if fragment_count > 0:
                    percent = (fragment_index / fragment_count) * 100
                else:
                    percent = 0
            
            speed = d.get('speed', 0)
            if speed and speed > 0:
                if speed >= 1024 * 1024:
                    speed_str = f"{speed / 1024 / 1024:.1f} MB/s"
                else:
                    speed_str = f"{speed / 1024:.0f} KB/s"
            else:
                speed_str = "--"
            
            eta = d.get('eta', 0)
            if eta and eta > 0:
                if eta >= 3600:
                    eta_str = f"{eta // 3600}h {(eta % 3600) // 60}m"
                elif eta >= 60:
                    eta_str = f"{eta // 60}m {eta % 60}s"
                else:
                    eta_str = f"{eta}s"
            else:
                eta_str = "--"
            
            filename = d.get('filename', '').split('\\')[-1].split('/')[-1]
            
            download_progress[session_id].update({
                'status': 'downloading',
                'progress': round(percent, 1),
                'speed': speed_str,
                'eta': eta_str,
                'filename': filename,
                'filesize': format_filesize(total) if total else '--',
                'downloaded': format_filesize(downloaded) if downloaded else '--'
            })
            
        elif d['status'] == 'finished':
            download_progress[session_id].update({
                'status': 'processing',
                'progress': 100,
                'speed': '⚡',
                'eta': 'Merging...'
            })
    
    try:
        # First, get video info for history (use extract_flat for speed if just need basic info)
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'extract_flat': False}) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Unknown')
            video_thumbnail = info.get('thumbnail', '')
            video_duration = info.get('duration', 0)
            video_id = info.get('id', '')
        
        download_progress[session_id]['title'] = video_title
        download_progress[session_id]['thumbnail'] = video_thumbnail
        download_progress[session_id]['status'] = 'downloading'
        
        # Base options - optimized for speed
        base_opts = {
            'paths': {'home': save_path},
            'outtmpl': '%(title)s.%(ext)s',
            'ffmpeg_location': FFMPEG_PATH,
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
            'prefer_free_formats': False,
            'check_formats': False,
            'retries': 3,
            'fragment_retries': 3,
            # Speed optimizations
            'concurrent_fragment_downloads': 4,  # Download fragments in parallel
            'buffersize': 1024 * 64,  # 64KB buffer
            'http_chunk_size': 10485760,  # 10MB chunks
            'throttledratelimit': None,  # No throttling
            'socket_timeout': 30,
            'nocheckcertificate': True,
            'prefer_insecure': True,
            'cachedir': False,
        }
        
        # Subtitle options
        if download_subtitles and subtitle_lang:
            if subtitle_lang.startswith('auto-'):
                lang = subtitle_lang[5:]
                base_opts['writeautomaticsub'] = True
                base_opts['subtitleslangs'] = [lang]
            else:
                base_opts['writesubtitles'] = True
                base_opts['subtitleslangs'] = [subtitle_lang]
            base_opts['subtitlesformat'] = 'srt/vtt/best'
        
        # Thumbnail option
        if download_thumbnail:
            base_opts['writethumbnail'] = True
        
        # Configure format based on resolution
        if str(resolution).startswith('mp3') or str(resolution).startswith('m4a') or str(resolution) in ['flac', 'wav', 'audio']:
            # Audio only
            audio_codec = 'mp3'
            audio_quality = '320'
            
            if resolution == 'mp3-320':
                audio_codec, audio_quality = 'mp3', '320'
            elif resolution == 'mp3-192':
                audio_codec, audio_quality = 'mp3', '192'
            elif resolution == 'mp3-128':
                audio_codec, audio_quality = 'mp3', '128'
            elif resolution == 'm4a':
                audio_codec, audio_quality = 'm4a', '256'
            elif resolution == 'flac':
                audio_codec, audio_quality = 'flac', 'best'
            elif resolution == 'wav':
                audio_codec, audio_quality = 'wav', 'best'
            elif resolution == 'audio':
                audio_codec, audio_quality = 'mp3', '320'
            
            ydl_opts = {
                **base_opts,
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_codec,
                    'preferredquality': audio_quality,
                }],
            }
            final_ext = audio_codec
            
        else:
            # Video with audio
            height = int(resolution)
            ydl_opts = {
                **base_opts,
                'format': f'bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={height}][ext=webm]+bestaudio[ext=webm]/bestvideo[height<={height}]+bestaudio/best[height<={height}]/best',
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
            }
            final_ext = 'mp4'
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Determine final filename
        safe_title = sanitize_filename(video_title)
        final_filename = f"{safe_title}.{final_ext}"
        final_path = os.path.join(save_path, final_filename)
        
        # Get actual file size
        actual_filesize = "Unknown"
        if os.path.exists(final_path):
            actual_filesize = format_filesize(os.path.getsize(final_path))
        
        # Add to history
        history_entry = {
            'id': session_id,
            'title': video_title,
            'thumbnail': video_thumbnail,
            'duration': format_duration(video_duration),
            'resolution': str(resolution),
            'filename': final_filename,
            'path': final_path,
            'filesize': actual_filesize,
            'timestamp': datetime.now().isoformat(),
            'video_id': video_id
        }
        download_history.insert(0, history_entry)
        
        # Trim history if needed
        if len(download_history) > MAX_HISTORY:
            download_history.pop()
        
        download_progress[session_id].update({
            'status': 'complete',
            'progress': 100,
            'complete': True,
            'filename': final_filename,
            'filesize': actual_filesize,
            'path': final_path
        })
        
    except Exception as e:
        error_msg = str(e)
        if 'cancelled' in error_msg.lower():
            download_progress[session_id].update({
                'status': 'cancelled',
                'error': 'Download cancelled',
                'complete': True
            })
        else:
            download_progress[session_id].update({
                'status': 'error',
                'error': error_msg,
                'complete': True
            })
    finally:
        # Clean up active downloads
        if session_id in active_downloads:
            del active_downloads[session_id]


@app.route('/progress/<session_id>')
def get_progress(session_id):
    """
    Server-Sent Events endpoint for streaming download progress.
    """
    def generate():
        retry_count = 0
        max_retries = 5
        last_progress = -1
        
        while True:
            if session_id in download_progress:
                progress = download_progress[session_id]
                current_progress = progress.get('progress', 0)
                
                # Only send update if progress changed or status changed
                if current_progress != last_progress or progress.get('complete'):
                    yield f"data: {json.dumps(progress)}\n\n"
                    last_progress = current_progress
                
                if progress.get('complete'):
                    # Clean up after sending final status
                    time.sleep(0.3)
                    if session_id in download_progress:
                        del download_progress[session_id]
                    if session_id in progress_queues:
                        del progress_queues[session_id]
                    break
                retry_count = 0
            else:
                retry_count += 1
                if retry_count > max_retries:
                    yield f"data: {json.dumps({'error': 'Session not found'})}\n\n"
                    break
            
            time.sleep(0.1)  # Faster updates for smoother progress
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@app.route('/history', methods=['GET'])
def get_history():
    """Get download history."""
    return jsonify({
        'success': True,
        'history': download_history
    })


@app.route('/history/clear', methods=['POST'])
def clear_history():
    """Clear download history."""
    global download_history
    download_history = []
    return jsonify({'success': True, 'message': 'History cleared'})


@app.route('/active', methods=['GET'])
def get_active_downloads():
    """Get list of active downloads."""
    active = []
    for session_id, progress in download_progress.items():
        if not progress.get('complete'):
            active.append({
                'session_id': session_id,
                **progress
            })
    return jsonify({
        'success': True,
        'active': active
    })


@app.route('/open_folder', methods=['POST'])
def open_folder():
    """Open a folder in Windows Explorer."""
    data = request.get_json()
    path = data.get('path', '')
    
    if path and os.path.exists(path):
        if os.path.isfile(path):
            # If it's a file, open the containing folder and select the file
            os.system(f'explorer /select,"{path}"')
        else:
            # If it's a folder, just open it
            os.startfile(path)
        return jsonify({'success': True})
    return jsonify({'error': 'Path not found'}), 404


@app.route('/check_ffmpeg', methods=['GET'])
def check_ffmpeg():
    """Check if ffmpeg is installed and accessible."""
    # First check for local ffmpeg in app directory
    if os.path.exists(FFMPEG_PATH):
        # Get version info
        try:
            import subprocess
            result = subprocess.run([FFMPEG_PATH, '-version'], 
                                    capture_output=True, text=True, timeout=5)
            version = result.stdout.split('\n')[0] if result.stdout else 'Unknown version'
        except:
            version = 'Unknown version'
        
        return jsonify({
            'installed': True,
            'path': FFMPEG_PATH,
            'version': version
        })
    
    # Then check system PATH
    ffmpeg_path = shutil.which('ffmpeg')
    
    if ffmpeg_path:
        try:
            import subprocess
            result = subprocess.run([ffmpeg_path, '-version'], 
                                    capture_output=True, text=True, timeout=5)
            version = result.stdout.split('\n')[0] if result.stdout else 'Unknown version'
        except:
            version = 'Unknown version'
        
        return jsonify({
            'installed': True,
            'path': ffmpeg_path,
            'version': version
        })
    else:
        return jsonify({
            'installed': False,
            'message': 'ffmpeg is not installed or not in PATH. Please install it for video merging to work.'
        })


@app.route('/download_thumbnail', methods=['POST'])
def download_thumbnail_image():
    """Download video thumbnail to a specified folder."""
    data = request.get_json()
    thumbnail_url = data.get('url', '')
    save_path = data.get('save_path', '')
    filename = data.get('filename', 'thumbnail')
    
    if not thumbnail_url or not save_path:
        return jsonify({'error': 'Missing URL or save path'}), 400
    
    try:
        # Sanitize filename
        safe_filename = sanitize_filename(filename)
        
        # Determine extension from URL
        ext = 'jpg'
        if '.png' in thumbnail_url:
            ext = 'png'
        elif '.webp' in thumbnail_url:
            ext = 'webp'
        
        filepath = os.path.join(save_path, f"{safe_filename}_thumbnail.{ext}")
        
        # Download thumbnail
        urllib.request.urlretrieve(thumbnail_url, filepath)
        
        return jsonify({
            'success': True,
            'path': filepath
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("\n" + "="*60)
    print("  YouTube Video Downloader - Enhanced Edition")
    print("="*60)
    print("\n  Starting server at: http://localhost:5000")
    print("\n  Features:")
    print("  • Video downloads (144p to 8K)")
    print("  • Audio extraction (MP3, M4A, FLAC, WAV)")
    print("  • Subtitle downloads")
    print("  • Thumbnail downloads")
    print("  • Download history")
    print("  • Playlist support")
    print("\n  IMPORTANT: Make sure ffmpeg is installed!")
    print("  - Windows: choco install ffmpeg")
    print("  - Or download from: https://ffmpeg.org/download.html")
    print("\n" + "="*60 + "\n")
    
    app.run(host='localhost', port=5000, debug=True, threaded=True)
