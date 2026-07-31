import pytest
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool

# 1. Override the database variables globally in app.database module BEFORE importing app.main
import app.database as database
SQLALCHEMY_DATABASE_URL = "sqlite://"
test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

database.engine = test_engine
database.SessionLocal = TestingSessionLocal

# Prevent init_db from recreating tables on the wrong engine
def mock_init_db():
    database.Base.metadata.create_all(bind=test_engine)
# 2. Now import app.main and override its symbols directly
import app.main as main
main.SessionLocal = TestingSessionLocal

from fastapi.testclient import TestClient
from app.main import app, get_db
from app.database import Base, MediaFile, FileStatus

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

# Patch both FastAPI overrides and main module get_db symbol reference
app.dependency_overrides[get_db] = override_get_db
main.get_db = override_get_db
client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    # Force recreate tables for every test to keep tests completely isolated
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    # Add dummy entries for sorting/pagination testing
    files = [
        MediaFile(filepath=f"/media/show/File_{i}.mkv", filename=f"File_{i}.mkv", status=FileStatus.PENDING)
        for i in range(1, 11)
    ]
    # Add one flagged title
    files.append(MediaFile(filepath="/media/show/Flagged.mkv", filename="Flagged.mkv", status=FileStatus.FLAGGED_TITLE))
    db.add_all(files)
    db.commit()
    db.close()
    yield
    # Cleanup
    Base.metadata.drop_all(bind=test_engine)

def test_get_files_pagination():
    response = client.get("/api/files?page=1&page_size=5")
    assert response.status_code == 200
    data = response.json()
    assert data["total_items"] == 11
    assert len(data["items"]) == 5

def test_get_files_filtering():
    response = client.get("/api/files?status=FLAGGED_TITLE")
    assert response.status_code == 200
    data = response.json()
    assert data["total_items"] == 1
    assert data["items"][0]["filename"] == "Flagged.mkv"

def test_requeue_by_status():
    # Verify we have 1 flagged file
    resp = client.get("/api/files?status=FLAGGED_TITLE")
    assert resp.json()["total_items"] == 1
    
    # Trigger requeue
    requeue_resp = client.post("/api/pipeline/requeue-by-status?status=FLAGGED_TITLE")
    assert requeue_resp.status_code == 200
    assert "Successfully requeued" in requeue_resp.json()["message"]
    
    # Verify it is now PENDING
    resp_after = client.get("/api/files?status=FLAGGED_TITLE")
    assert resp_after.json()["total_items"] == 0

def test_get_failures_log(tmp_path, monkeypatch):
    # Setup mock failures.log content
    log_content = "=== FAILURE LOG ===\nFile: test.mkv\nStatus: FLAGGED_TITLE\n"
    
    # Override main.WORKSPACE_ROOT temporarily for this test
    monkeypatch.setattr(main, "WORKSPACE_ROOT", str(tmp_path))
    
    # Create the mock file
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    failures_file = data_dir / "failures.log"
    failures_file.write_text(log_content, encoding="utf-8")
    
    # Make request
    response = client.get("/api/pipeline/failures-log")
    assert response.status_code == 200
    assert response.json()["log"] == log_content

def test_scan_sample_files(tmp_path):
    from app.scanner import MediaScanner
    from app.database import MediaFile, FileStatus, AuditJob
    
    # Setup scanner directories
    scan_dir = tmp_path / "media"
    scan_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a mock normal video file (empty)
    movie_file = scan_dir / "Inception.2010.mkv"
    movie_file.write_text("dummy content", encoding="utf-8")
    
    # Create a mock sample file (empty, which will have size < 150MB)
    sample_file = scan_dir / "Inception.2010-sample.mkv"
    sample_file.write_text("dummy content", encoding="utf-8")
    
    # Run scanner
    scanner = MediaScanner(media_directories=[str(scan_dir)])
    db = TestingSessionLocal()
    
    # Create tables
    Base.metadata.create_all(bind=test_engine)
    
    count = scanner.scan_and_register_files(db)
    assert count == 2
    
    # Query database to assert statuses
    movie_record = db.query(MediaFile).filter(MediaFile.filename == "Inception.2010.mkv").first()
    assert movie_record.status == FileStatus.PENDING
    
    # The normal file should have an active job enqueued
    job_record = db.query(AuditJob).filter(AuditJob.media_file_id == movie_record.id).first()
    assert job_record is not None
    
    sample_record = db.query(MediaFile).filter(MediaFile.filename == "Inception.2010-sample.mkv").first()
    assert sample_record.status == FileStatus.FLAGGED_SAMPLE
    
    # The sample file should NOT have a job enqueued
    sample_job = db.query(AuditJob).filter(AuditJob.media_file_id == sample_record.id).first()
    assert sample_job is None
    
    db.close()

def test_index_html_dom_elements():
    """Asserts that index.html contains all expected DOM IDs to prevent JavaScript null reference errors."""
    # Find static index.html path relative to app directory
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    index_path = os.path.join(base_dir, "app", "static", "index.html")
    assert os.path.exists(index_path)
    
    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    required_ids = [
        "modal-title",
        "modal-status-badge",
        "modal-confidence-badge",
        "modal-notes",
        "meta-container",
        "meta-vcodec",
        "meta-acodec",
        "meta-variance",
        "modal-keyframes",
        "audio-detected-lang",
        "audio-transcript",
        "details-modal"
    ]
    
    for element_id in required_ids:
        # Simple string assertion checking for id attribute existence
        assert f'id="{element_id}"' in content or f"id='{element_id}'" in content, f"Missing expected ID: {element_id}"
