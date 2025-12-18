#!/usr/bin/env bash
# exit on error
set -o errexit

# Install python dependencies
pip install -r requirements.txt

# Download and setup static ffmpeg
if [ ! -f ./ffmpeg ]; then
    echo "Downloading ffmpeg..."
    curl -L https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz -o ffmpeg.tar.xz
    tar -xf ffmpeg.tar.xz --strip-components=2 ffmpeg-master-latest-linux64-gpl/bin/ffmpeg
    rm ffmpeg.tar.xz
    chmod +x ffmpeg
fi

echo "Build complete."
