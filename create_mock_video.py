import subprocess
import os
from pathlib import Path

def create_mock_video():
    output_dir = Path("c:/Users/jrust/Documents/PlexScraper/media")
    output_dir.mkdir(exist_ok=True)
    
    target_file = output_dir / "test_movie.mp4"
    if target_file.exists():
        print(f"Mock video already exists at {target_file}")
        return
        
    print("Generating a 10-second mock MP4 video for integration testing...")
    
    # ffmpeg command to create 10-second black video with sine audio
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=640x360:d=10",
        "-f", "lavfi",
        "-i", "sine=f=440:d=10",
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(target_file)
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print(f"Mock video generated successfully at {target_file}!")
    except Exception as e:
        print(f"Error generating mock video: {e}")

if __name__ == "__main__":
    create_mock_video()
