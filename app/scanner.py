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
                            # 1. Identify sample files: contains 'sample' in name and file size is less than 150MB
                            is_sample = False
                            if "sample" in file.lower():
                                try:
                                    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                                    if file_size_mb < 150: # under 150MB
                                        is_sample = True
                                except Exception:
                                    pass
                                    
                            initial_status = FileStatus.FLAGGED_SAMPLE if is_sample else FileStatus.PENDING
                            
                            # Create new MediaFile record
                            media_file = MediaFile(
                                filepath=absolute_path,
                                filename=file,
                                title=file_path.stem,
                                status=initial_status
                            )
                            db.add(media_file)
                            db.commit()
                            db.refresh(media_file)
                            
                            # If it's a sample file, write a quick mock audit result and skip queue enqueuing
                            if is_sample:
                                from app.database import AuditResult
                                result = AuditResult(
                                    media_file_id=media_file.id,
                                    ffprobe_valid=True,
                                    status=FileStatus.FLAGGED_SAMPLE,
                                    notes="Identified as a sample media file clip (file size < 150MB with 'sample' in filename)."
                                )
                                db.add(result)
                                db.commit()
                                logger.info(f"Identified and flagged sample file: {file}")
                            else:
                                # Add an associated audit job automatically for normal files
                                job = AuditJob(
                                    media_file_id=media_file.id,
                                    status=JobStatus.PENDING
                                )
                                db.add(job)
                                db.commit()
                                
                            new_files_count += 1
                            if not is_sample:
                                logger.info(f"Discovered new media: {file}")
                            
        return new_files_count
