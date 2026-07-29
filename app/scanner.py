import os
import logging
from pathlib import Path
from sqlalchemy.orm import Session
from app.database import MediaFile, AuditJob, FileStatus, JobStatus

logger = logging.getLogger("scanner")

# Supported media extensions
MEDIA_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov"}

class MediaScanner:
    def __init__(self, media_directories: list):
        self.media_directories = [Path(d) for d in media_directories]

    def scan_and_register_files(self, db: Session) -> int:
        """Crawls target directories, registers new files in DB, and adds them to queue."""
        new_files_count = 0
        
        for directory in self.media_directories:
            if not directory.exists():
                logger.warning(f"Scan path does not exist: {directory}")
                continue
                
            logger.info(f"Scanning directory: {directory}")
            for root, _, files in os.walk(directory):
                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix.lower() in MEDIA_EXTENSIONS:
                        absolute_path = str(file_path.resolve())
                        
                        # Check if file is already in the database
                        existing_file = db.query(MediaFile).filter(MediaFile.filepath == absolute_path).first()
                        if not existing_file:
                            # Create new MediaFile record
                            media_file = MediaFile(
                                filepath=absolute_path,
                                filename=file,
                                title=file_path.stem,
                                status=FileStatus.PENDING
                            )
                            db.add(media_file)
                            db.commit()
                            db.refresh(media_file)
                            
                            # Add an associated audit job automatically
                            job = AuditJob(
                                media_file_id=media_file.id,
                                status=JobStatus.PENDING
                            )
                            db.add(job)
                            db.commit()
                            
                            new_files_count += 1
                            logger.info(f"Discovered new media: {file}")
                            
        return new_files_count
