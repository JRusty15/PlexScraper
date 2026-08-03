import os
import json
import subprocess
import sys
import psutil
from pathlib import Path

# Throttled subprocess runner
def run_throttled_process(cmd, timeout=180):
    """Runs a process with below-normal priority to prevent CPU/IO spikes with a safety timeout."""
    creationflags = 0
    if sys.platform == "win32":
        # BELOW_NORMAL_PRIORITY_CLASS
        creationflags = 0x00004000
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags
    )
    
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise TimeoutError(f"Process timed out after {timeout} seconds: {' '.join(cmd)}")

class MediaProcessor:
    def __init__(self, output_dir: str = "./processed_media"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.keyframes_dir = self.output_dir / "keyframes"
        self.audio_dir = self.output_dir / "audio"
        self.keyframes_dir.mkdir(exist_ok=True)
        self.audio_dir.mkdir(exist_ok=True)

    def probe_media(self, filepath: str) -> dict:
        """Probes structural integrity and metadata using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            filepath
        ]
        
        returncode, stdout, stderr = run_throttled_process(cmd, timeout=30)
        if returncode != 0:
            raise RuntimeError(f"ffprobe failed with exit code {returncode}: {stderr.decode('utf-8', errors='ignore')}")
            
        return json.loads(stdout.decode("utf-8"))

    def parse_metadata(self, filepath: str) -> dict:
        """Extracts parsed attributes: duration, tracks, formats."""
        data = self.probe_media(filepath)
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        
        duration = float(fmt.get("duration", 0))
        container = fmt.get("format_name", "")
        
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        video_codec = video_stream.get("codec_name", "")
        
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        audio_codec = audio_streams[0].get("codec_name", "") if audio_streams else None
        
        audio_tracks = []
        for idx, s in enumerate(audio_streams):
            tags = s.get("tags", {})
            lang = tags.get("language", "und")
            title = tags.get("title", "")
            audio_tracks.append({
                "index": s.get("index"),
                "codec": s.get("codec_name"),
                "language": lang,
                "title": title,
                "channels": s.get("channels")
            })

        return {
            "duration": duration,
            "container": container,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "audio_tracks": audio_tracks
        }

    def extract_keyframes(self, filepath: str, duration: float, media_id: int) -> list:
        """Extracts keyframes at 1m, 2m, 3m, 4m, 5m (for title card checks) and strategic percentages (for sanity checks)."""
        extracted_paths = []
        
        # We sample:
        # Title check timestamps (in seconds): shifted earlier to catch sitcom intros (usually between 30s - 75s)
        title_times = [30, 45, 75, 120, 240]
        # Content sanity timestamps (percentages): 25%, 50%, 75%, 90% (credits)
        pct_times = [0.25 * duration, 0.50 * duration, 0.75 * duration, 0.90 * duration]
        
        # Combine and sort unique timestamps
        timestamps = []
        for t in title_times:
            if t < duration:
                timestamps.append(t)
        for t in pct_times:
            if t < duration and t not in timestamps:
                timestamps.append(t)
        timestamps = sorted(list(set(timestamps)))
        
        for idx, timestamp in enumerate(timestamps):
            out_filename = f"media_{media_id}_frame_{idx}.jpg"
            out_path = self.keyframes_dir / out_filename
            
            # Extract 1 frame at target timestamp, low priority thread
            ffmpeg_threads = os.environ.get("FFMPEG_THREADS", "1")
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(timestamp),
                "-threads", ffmpeg_threads,
                "-i", filepath,
                "-vframes", "1",
                "-vf", "scale=640:-1",
                "-q:v", "8",
                str(out_path)
            ]
            
            returncode, stdout, stderr = run_throttled_process(cmd, timeout=180)
            if returncode == 0 and out_path.exists():
                extracted_paths.append(f"/static/media/keyframes/{out_filename}")
                
        return extracted_paths
 
    def extract_audio_clips(self, filepath: str, duration: float, media_id: int) -> list:
        """Extracts 3x 20s mono wav clips at 30%, 50%, 70% of duration from non-silent regions."""
        percentages = [30, 50, 70]
        extracted_paths = []
        ffmpeg_threads = os.environ.get("FFMPEG_THREADS", "1")
        
        for idx, pct in enumerate(percentages):
            timestamp = (pct / 100.0) * duration
            out_filename = f"media_{media_id}_clip_{idx}.wav"
            out_path = self.audio_dir / out_filename
            
            # Extract 20 seconds, resample to 16kHz mono (ideal for Whisper)
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(timestamp),
                "-threads", ffmpeg_threads,
                "-i", filepath,
                "-t", "20",
                "-ac", "1",
                "-ar", "16000",
                str(out_path)
            ]
            
            returncode, stdout, stderr = run_throttled_process(cmd, timeout=180)
            if returncode == 0 and out_path.exists():
                extracted_paths.append(f"/static/media/audio/{out_filename}")
                
        return extracted_paths

    def extract_subtitles(self, filepath: str) -> str:
        """Attempts to extract text subtitles from the first English or default text subtitle track."""
        import re
        try:
            cmd_probe = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                filepath
            ]
            returncode, stdout, stderr = run_throttled_process(cmd_probe, timeout=20)
            if returncode == 0:
                probe_data = json.loads(stdout.decode("utf-8"))
                streams = probe_data.get("streams", [])
                
                # Filter subtitle streams
                sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
                
                # Look for a text-based subtitle track (subrip/srt, ass, ssa, mov_text, webvtt)
                text_codecs = ["subrip", "srt", "ass", "ssa", "mov_text", "webvtt"]
                
                selected_index = None
                # Priority 1: English text-based subtitle
                for s in sub_streams:
                    codec = s.get("codec_name", "").lower()
                    lang = s.get("tags", {}).get("language", "").lower()
                    if codec in text_codecs and (lang == "eng" or lang == "en"):
                        selected_index = s.get("index")
                        break
                        
                # Priority 2: Any text-based subtitle
                if selected_index is None:
                    for s in sub_streams:
                        codec = s.get("codec_name", "").lower()
                        if codec in text_codecs:
                            selected_index = s.get("index")
                            break
                            
                # Priority 3: Fallback to first subtitle stream generally
                if selected_index is None and sub_streams:
                    selected_index = sub_streams[0].get("index")
                    
                if selected_index is not None:
                    cmd_ffmpeg = [
                        "ffmpeg",
                        "-y",
                        "-i", filepath,
                        "-map", f"0:{selected_index}",
                        "-f", "srt",
                        "-"
                    ]
                    rc, stdout_ff, stderr_ff = run_throttled_process(cmd_ffmpeg, timeout=30)
                    if rc == 0:
                        raw_text = stdout_ff.decode("utf-8", errors="ignore")
                        
                        # Clean SRT format formatting
                        # Remove subtitle indexes
                        clean = re.sub(r'^\d+\s*$', '', raw_text, flags=re.MULTILINE)
                        # Remove timestamps
                        clean = re.sub(r'^\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*$', '', clean, flags=re.MULTILINE)
                        # Remove HTML tags
                        clean = re.sub(r'<[^>]*>', '', clean)
                        # Remove empty lines
                        lines = [line.strip() for line in clean.split('\n') if line.strip()]
                        return "\n".join(lines)
        except Exception:
            pass
            
        return ""

    def extract_keyframes_dense(self, filepath: str, duration: float, media_id: int) -> list:
        """Extracts dense keyframes every 90 seconds (up to 25 frames) for extended visual verification."""
        extracted_paths = []
        interval = 90
        # Calculate timestamps
        timestamps = list(range(30, int(duration), interval))
        # Cap at 25 frames
        if len(timestamps) > 25:
            step = len(timestamps) / 25.0
            timestamps = [timestamps[int(i * step)] for i in range(25)]
            
        for idx, timestamp in enumerate(timestamps):
            out_filename = f"media_{media_id}_dense_frame_{idx}.jpg"
            out_path = self.keyframes_dir / out_filename
            
            ffmpeg_threads = os.environ.get("FFMPEG_THREADS", "1")
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(timestamp),
                "-threads", ffmpeg_threads,
                "-i", filepath,
                "-vframes", "1",
                "-vf", "scale=480:-1",
                "-q:v", "9",
                str(out_path)
            ]
            
            try:
                returncode, stdout, stderr = run_throttled_process(cmd, timeout=180)
                if returncode == 0 and out_path.exists():
                    extracted_paths.append(f"/static/media/keyframes/{out_filename}")
            except Exception:
                pass
                
        return extracted_paths
