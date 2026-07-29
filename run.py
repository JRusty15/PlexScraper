import uvicorn
import os

if __name__ == "__main__":
    # Ensure processed media output folders exist
    os.makedirs("./processed_media/keyframes", exist_ok=True)
    os.makedirs("./processed_media/audio", exist_ok=True)
    os.makedirs("./media", exist_ok=True)
    
    print("Starting PlexScraper Media Verification Service...")
    print("Dashboard available at: http://localhost:8000")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
