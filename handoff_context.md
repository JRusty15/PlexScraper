# Handoff Context: PlexScraper Media Verification Service

This document summarizes the current architecture, implementation status, and local environment settings for the **PlexScraper** project. Use this markdown content to boot up your Antigravity context on your laptop tomorrow.

---

## 1. Project Overview & Architecture
An asynchronous media auditor verifying naming, structural integrity, and audio tracks for files managed by Plex.
* **Backend:** FastAPI, SQLite (SQLAlchemy, NullPool to prevent connection leaks).
* **Scanner:** Periodically crawls mount paths, checks DB, and registers new files into the audit queue.
* **Worker Queue:** Sequential, single-threaded processing loop to prevent GPU VRAM overloading.
* **AI Pipelines:** 
  * **Ollama VLM (`qwen2.5vl`)** for title card verification (5% frame), credits checking (90% frame), and scene context checking (50% frame).
  * **Whisper (Language ID)** placeholder with standard en-speech fallback logic.
* **UI:** A modern dark-mode glassmorphism dashboard featuring pagination (50 files/page), debounced searching, queue controls (Pause, Resume, Nuke), and inspect modals with raw logs and keyframe grids.

---

## 2. Environment Details
* **Host OS:** Ubuntu Server GPU Node
* **Local Machine IP:** `10.0.0.242` (Ubuntu Server IP running Ollama & Plex)
* **Ollama API Port:** `11434` (requires model `qwen2.5vl` pulled)
* **NFS Network Shares (Unraid -> Ubuntu Server Host mounts):**
  * Movies share: `192.168.1.100:/mnt/user/Media/Movies` -> Mounted at `/mnt/unraid/movies`
  * TV Shows share: `192.168.1.100:/mnt/user/Media/TV Shows` -> Mounted at `/mnt/unraid/tv`
  * Mounted persistently on host in `/etc/fstab` (using `\040` for space in TV path).

---

## 3. Configuration & Docker Settings
* **Portainer Stack Compose (`docker-compose.yml`):**
```yaml
services:
  verifier:
    image: ghcr.io/jrusty15/plexscraper:latest
    container_name: plex-verifier
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=sqlite:////app/data/plex_verification.db
      - OLLAMA_API_URL=http://10.0.0.242:11434
      - OLLAMA_MODEL=qwen2.5vl
      - SCAN_PATHS=/media/Movies,/media/TV
      - PLEX_URL=http://10.0.0.242:32400
      - PLEX_TOKEN=your-plex-token-here
    volumes:
      - /opt/plexscraper/data:/app/data
      - /opt/plexscraper/processed_media:/app/processed_media
      - /mnt/unraid/movies:/media/Movies:ro
      - /mnt/unraid/tv:/media/TV:ro
```

---

## 4. Current Work State & Fixes Applied
1. **NameError SessionLocal Fix:** Added missing database imports in `main.py`.
2. **Database Timeout Leak Fix:** Replaced dependencies injection on files fetcher routes with explicit `try/finally` blocks and configured the SQLAlchemy engine with `NullPool` (ideal for SQLite concurrency).
3. **Queue Query Syntax Fix:** Fixed database syntax error on line 57 in `app/queue_manager.py`.
4. **VLM Parser & Verbose logs:** Added regex text fallback parser for Qwen2.5-VL to prevent crashes on non-JSON model returns. Extended database `notes` to store raw VLM outputs for quick troubleshooting in UI.
5. **Pagination & Debounced Search:** Rebuilt frontend API and Javascript state machines to load pages of 50 items and prevent 18k files DOM bottleneck.

---

## 5. Next Steps
* Update your Stack in Portainer, enable **Re-pull image**, and restart the container to run the latest master build.
* Perform a **Ctrl + F5** force-reload in the browser to clear JS cached code.
* Use the **Nuke Queue** button to wipe old legacy error logs, then trigger **Scan Directories** to start fresh!
