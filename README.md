# 🎬 YouTube Downloader Pro

A modern, beautiful web application for downloading YouTube videos and audio with a premium glassmorphism UI.

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.3+-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

## ✨ Features

- 🎥 **Video Downloads** - Support for 144p to 8K resolution
- 🎵 **Audio Extraction** - MP3, M4A, FLAC, WAV formats
- 📝 **Subtitles** - Download captions in multiple languages
- 🖼️ **Thumbnails** - Save video thumbnails
- 📋 **Batch Downloads** - Paste multiple URLs at once
- 🎯 **Drag & Drop** - Simply drag YouTube links onto the page
- 🔄 **Live Progress** - Beautiful circular progress indicator
- 🎨 **Premium UI** - Glassmorphism design with smooth animations

## 🚀 Quick Start

### Local Development

1. **Clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/youtube-downloader.git
   cd youtube-downloader
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install FFmpeg** (required for video merging)
   - Windows: `choco install ffmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html)
   - Mac: `brew install ffmpeg`
   - Linux: `apt install ffmpeg`

4. **Run the app**
   ```bash
   python app.py
   ```

5. **Open browser**
   Navigate to `http://localhost:5000`


## 📁 Project Structure

```
youtube-downloader/
├── app.py              # Flask backend
├── templates/
│   └── index.html      # Frontend (HTML/CSS/JS)
├── requirements.txt    # Python dependencies
├── render.yaml         # Render deployment config
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## ⚠️ Important Notes

- **FFmpeg Required**: Video/audio merging requires FFmpeg
- **Cloud Limitations**: On cloud platforms, files are streamed directly to browser (no server storage)
- **Rate Limits**: YouTube may rate-limit excessive requests

## 🛠️ Tech Stack

- **Backend**: Flask, yt-dlp
- **Frontend**: Vanilla JavaScript, CSS3
- **Fonts**: Inter, JetBrains Mono
- **Deployment**: Gunicorn, Render

## 📄 License

MIT License - feel free to use and modify!

---

Made with ❤️ using yt-dlp and Flask
