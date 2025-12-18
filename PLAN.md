# High-Level Architecture Plan: "StealthStream" YouTube Downloader

## 1. Executive Summary
This document outlines the architectural strategy to build a production-ready, evasion-resistant YouTube downloader deployable on PaaS providers like Render or Railway. The core differentiator from standard tools will be **"Zero-Footprint Streaming"** and **"Advanced Fingerprint Mimicry"** to bypass YouTube's aggressive bot detection (datacenter IP blocking).

## 2. Core Philosophy
To outperform tools like y2mate, we must prioritize:
1.  **Stealth:** The server must look like a residential user, not a cloud server.
2.  **Speed:** Minimize "Time to First Byte" (TTFB). The user should see the download start immediately, not wait for the server to download it first.
3.  **Ephemerality:** The server filesystem should be treated as temporary. No long-term storage.

## 3. The "Bot Detection" Challenge & Solution
YouTube blocks requests from known datacenter IP ranges (AWS, GCP, Azure, which Render/Railway use).

### Strategy A: Advanced Header & Fingerprint Rotation
We will not just use a random User-Agent. We will implement a coherent **Client Profile System**.
*   **The Profile:** A specific combination of `User-Agent`, `Sec-Ch-Ua`, `Accept-Language`, and internal `yt-dlp` client identifiers (e.g., mimicking an Android TV client or an iOS client).
*   **PO Token (Proof of Origin):** We will implement a mechanism to generate or retrieve valid `PO Tokens` (visitor data). This is crucial for accessing 1080p+ streams without throttling.
*   **Traffic Shaping:** Randomize sleep intervals slightly between metadata fetches to avoid machine-like patterns.

### Strategy B: The "Stream Proxy" Architecture (Bypassing IP Bans)
Since the server IP might be flagged, the architecture will support **Residential Proxy Injection**.
*   The application will look for a `PROXY_URL` environment variable.
*   If present, all traffic to `*.googlevideo.com` and `*.youtube.com` will be tunneled through this proxy.
*   *Note:* For the free deployment, we will attempt to use IPv6 rotation (supported by some PaaS) or fallback to "Android Client" mimicry which is often less strictly rate-limited.

## 4. Implementation Logic: The "Hybrid Pipeline"

We will implement two distinct download pipelines based on the requested quality. This is the key to efficiency.

### Pipeline A: The "Direct Pipe" (For 720p / Audio Only)
Used when video and audio are in a single file (Format 22) or when downloading just audio.
1.  **Fetch:** Server opens a connection to the underlying Google Video URL.
2.  **Pipe:** As chunks of data arrive at the server, they are immediately flushed to the user's browser response.
3.  **Result:** Zero disk usage, instant download start.
4.  **Tech:** Python Generators + Flask `stream_with_context`.

### Pipeline B: The "Merge & Purge" (For 1080p / 4K)
Used when High Quality is requested (YouTube separates Audio and Video streams for HQ).
1.  **Acquire:** Download Video track and Audio track simultaneously to a unique temporary ID folder (`/tmp/{session_id}/`).
2.  **Process:** Use `ffmpeg` to merge container types (MP4) effectively.
3.  **Serve:** Stream the resulting file to the user.
4.  **Cleanup:** A strict "Garbage Collector" background thread ensures the file is deleted immediately after the response closes or if the connection drops.
5.  **Optimization:** Use `aria2c` as the external downloader for `yt-dlp` to utilize multi-connection downloading, saturating the server's bandwidth to finish the download phase faster.

## 5. Technical Stack & Deployment

*   **Backend:** Flask (Lightweight, robust).
*   **WSGI Server:** Gunicorn with `gthread` workers.
    *   *Reason:* Downloading is I/O bound. Threaded workers allow handling multiple user downloads concurrently without blocking the application.
*   **Engine:** `yt-dlp` (The gold standard, kept up-to-date).
*   **Processing:** `ffmpeg` (Static build included in repo).
*   **Caching:** Simple in-memory LRU cache (or Redis if available) for video metadata.
    *   *Why?* If User A and User B request the same video, we don't fetch info from YouTube twice.

## 6. Detailed Workflow

### Step 1: The "Analyze" Phase
1.  User submits URL.
2.  Server (masking as an iOS client) fetches metadata.
3.  Server caches the `googlevideo` direct links for 1 hour.
4.  Server returns a JSON object with formatted options (Resolution, Filesize, Codec).

### Step 2: The "Download" Phase
1.  User selects "1080p MP4".
2.  Browser sends POST request to `/stream_video`.
3.  **Server Action:**
    *   Checks if quality requires merging.
    *   **If Yes (1080p):** Initiates `yt-dlp` download to `/tmp`. Updates a server-side progress dict. Frontend polls `/progress` to show a bar. Once merged, the server sends the file blob.
    *   **If No (720p):** Server constructs a `Response` object that iterates over the `requests.get(googlevideo_url, stream=True)` content.
    *   Sets headers: `Content-Disposition: attachment; filename="video.mp4"`.

## 7. Safety & "No-Logs" Policy
*   No IP logging of users.
*   Filenames are sanitized aggressively to prevent filesystem command injection.
*   The `/tmp` directory is wiped on server startup and monitored by a cleanup task every 60 seconds.

## 8. Why this beats y2mate?
*   **No Ads/Popups:** Clean interface.
*   **Higher Reliability:** By using fresh `yt-dlp` builds and custom headers.
*   **Privacy:** No tracking.
*   **Audio Quality:** We will default to the highest bitrate audio (often 128k or 160k opus) converted to 320k MP3/M4A, whereas many sites compress audio.

## 9. Next Steps (Development Phase)
1.  Configure `web_hosting/app.py` to use the Generator Streaming pattern.
2.  Implement the `BackgroundCleaner` class.
3.  Setup `gunicorn` config for Render/Railway.
4.  Refine `yt-dlp` options for maximum stealth.
