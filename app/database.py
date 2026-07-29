import os
from datetime import datetime
from enum import Enum
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Enum as SQLEnum, Text, Boolean
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./plex_verification.db")

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=NullPool
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class FileStatus(str, Enum):
    PENDING = "PENDING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    FLAGGED_DURATION = "FLAGGED_DURATION"
    FLAGGED_LANGUAGE = "FLAGGED_LANGUAGE"
    FLAGGED_TITLE = "FLAGGED_TITLE"
    FLAGGED_CORRUPT = "FLAGGED_CORRUPT"
    FAILED = "FAILED"

class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"

class MediaFile(Base):
    __tablename__ = "media_files"

    id = Column(Integer, primary_key=True, index=True)
    filepath = Column(String, unique=True, index=True, nullable=False)
    filename = Column(String, nullable=False)
    title = Column(String, nullable=True)
    plex_rating_key = Column(String, nullable=True) # ID from Plex
    media_type = Column(String, nullable=True)      # 'movie' or 'episode'
    expected_duration = Column(Float, nullable=True) # in seconds, from Plex/TMDB
    added_at = Column(DateTime, default=datetime.utcnow)
    status = Column(SQLEnum(FileStatus), default=FileStatus.PENDING)
    
    # Relationships
    results = relationship("AuditResult", back_populates="media_file", cascade="all, delete-orphan")
    jobs = relationship("AuditJob", back_populates="media_file", cascade="all, delete-orphan")

class AuditJob(Base):
    __tablename__ = "audit_jobs"

    id = Column(Integer, primary_key=True, index=True)
    media_file_id = Column(Integer, ForeignKey("media_files.id"), nullable=False)
    status = Column(SQLEnum(JobStatus), default=JobStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    error_message = Column(Text, nullable=True)

    media_file = relationship("MediaFile", back_populates="jobs")

class AuditResult(Base):
    __tablename__ = "audit_results"

    id = Column(Integer, primary_key=True, index=True)
    media_file_id = Column(Integer, ForeignKey("media_files.id"), nullable=False)
    audited_at = Column(DateTime, default=datetime.utcnow)
    
    # Verification details
    ffprobe_valid = Column(Boolean, default=False)
    duration_actual = Column(Float, nullable=True)
    duration_variance = Column(Float, nullable=True) # percentage difference
    
    # Track Metadata
    video_codec = Column(String, nullable=True)
    audio_codec = Column(String, nullable=True)
    container_format = Column(String, nullable=True)
    audio_tracks_info = Column(Text, nullable=True)  # JSON representation of audio streams
    
    # Visual check
    vlm_title_verified = Column(Boolean, nullable=True)
    vlm_credits_verified = Column(Boolean, nullable=True)
    vlm_sanity_check_passed = Column(Boolean, nullable=True)
    vlm_raw_response = Column(Text, nullable=True)
    keyframes_paths = Column(Text, nullable=True)    # JSON list of saved keyframe paths
    
    # Audio check
    detected_languages = Column(String, nullable=True) # e.g. "en, es"
    audio_transcript_snippet = Column(Text, nullable=True)
    audio_clips_paths = Column(Text, nullable=True)  # JSON list of audio clip paths
    
    # Final Result
    status = Column(SQLEnum(FileStatus), nullable=False)
    notes = Column(Text, nullable=True)

    media_file = relationship("MediaFile", back_populates="results")

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
