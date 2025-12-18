#!/usr/bin/env bash
# exit on error
set -o errexit

# Install python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Download and setup static ffmpeg
if [ ! -f ./ffmpeg ]; then
    echo "Downloading ffmpeg..."
    curl -L https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz -o ffmpeg.tar.xz
    
    echo "Extracting ffmpeg..."
    tar -xf ffmpeg.tar.xz
    
    echo "Moving binary..."
    # Find the ffmpeg binary inside the extracted folders and move it to root
    find . -type f -name "ffmpeg" -exec mv {} . \;
    
    # Cleanup
    rm -rf ffmpeg-master-* ffmpeg.tar.xz
    chmod +x ffmpeg
    echo "ffmpeg installed successfully"
fi