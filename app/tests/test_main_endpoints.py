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
    
    # Override main.get_workspace_root temporarily for this test
    monkeypatch.setattr(main, "get_workspace_root", lambda: tmp_path)
    
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
        "details-modal",
        "active-jobs-panel",
        "active-jobs-list",
        "label-max-jobs",
        "label-ffmpeg-threads",
        "input-max-jobs",
        "input-ffmpeg-threads",
        "input-ollama-url",
        "input-ollama-model",
        "input-scan-paths",
        "input-workspace-root",
        "input-plex-url",
        "input-plex-token"
    ]
    
    for element_id in required_ids:
        # Simple string assertion checking for id attribute existence
        assert f'id="{element_id}"' in content or f"id='{element_id}'" in content, f"Missing expected ID: {element_id}"

def test_pipeline_config_endpoints():
    # 1. Test GET config
    response = client.get("/api/pipeline/config")
    assert response.status_code == 200
    data = response.json()
    assert "max_concurrent_jobs" in data
    assert "ffmpeg_threads" in data
    assert "ollama_api_url" in data
    assert "ollama_model" in data
    assert "scan_paths" in data
    assert "workspace_root" in data
    assert "plex_url" in data
    assert "plex_token" in data
    
    # 2. Test POST config updates
    payload = {
        "max_concurrent_jobs": 5,
        "ffmpeg_threads": 3,
        "ollama_api_url": "http://ollama-test:11434",
        "ollama_model": "test-vlm-model",
        "scan_paths": "/media/test1,/media/test2",
        "workspace_root": "./test_workspace",
        "plex_url": "http://plex-test:32400",
        "plex_token": "test-plex-token-xyz"
    }
    post_resp = client.post("/api/pipeline/config", json=payload)
    assert post_resp.status_code == 200
    
    # 3. Verify GET config returns updated values
    get_resp = client.get("/api/pipeline/config")
    assert get_resp.status_code == 200
    updated_data = get_resp.json()
    assert updated_data["max_concurrent_jobs"] == 5
    assert updated_data["ffmpeg_threads"] == 3
    assert updated_data["ollama_api_url"] == "http://ollama-test:11434"
    assert updated_data["ollama_model"] == "test-vlm-model"
    assert updated_data["scan_paths"] == "/media/test1,/media/test2"
    assert updated_data["workspace_root"] == "./test_workspace"
    assert updated_data["plex_url"] == "http://plex-test:32400"
    assert updated_data["plex_token"] == "test-plex-token-xyz"

def test_worker_start_resets_stale_jobs():
    from app.database import AuditJob, JobStatus, FileStatus
    from app.queue_manager import QueueWorker
    
    db = TestingSessionLocal()
    
    # Clean previous records
    db.query(AuditJob).delete()
    db.query(MediaFile).delete()
    db.commit()
    
    # Create a verifying file and its corresponding processing job
    media_file = MediaFile(
        filepath="/media/Movies/Stale.mkv",
        filename="Stale.mkv",
        status=FileStatus.VERIFYING
    )
    db.add(media_file)
    db.commit()
    
    job = AuditJob(
        media_file_id=media_file.id,
        status=JobStatus.PROCESSING
    )
    db.add(job)
    db.commit()
    
    db.close()
    
    # Initialize a temporary worker pointing to the test DB configuration
    test_worker = QueueWorker(workspace_root=".")
    
    # Mock loop method to prevent launching real background runner
    async def mock_loop():
        pass
    test_worker._process_queue_loop = mock_loop
    
    test_worker.start()
    test_worker.stop()
    
    # Verify DB states
    db_verify = TestingSessionLocal()
    job_record = db_verify.query(AuditJob).first()
    file_record = db_verify.query(MediaFile).first()
    
    assert job_record.status == JobStatus.PENDING
    assert file_record.status == FileStatus.PENDING
    
    db_verify.close()

def test_run_throttled_process_timeout():
    import pytest
    from app.media_processor import run_throttled_process
    
    cmd = ["powershell", "-c", "Start-Sleep 10"]
    
    with pytest.raises(TimeoutError) as exc_info:
        run_throttled_process(cmd, timeout=1)
        
    assert "Process timed out after 1 seconds" in str(exc_info.value)
