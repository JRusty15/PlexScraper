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
        "input-plex-token",
        "btn-test-config",
        "test-results-panel",
        "test-results-list"
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

def test_pipeline_config_validation_endpoint(monkeypatch):
    # Mock httpx response for Ollama
    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data
        def json(self):
            return self._json_data
            
    async def mock_get(url):
        return MockResponse(200, {"models": [{"name": "qwen2.5-vl:latest"}]})
        
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda *args, **kwargs: mock_get(args[1]))
    
    # Mock Plex connection
    from plexapi import server
    def mock_plex_server(url, token):
        class MockServer:
            class MockLibrary:
                def sections(self):
                    class MockSection:
                        def __init__(self, title):
                            self.title = title
                    return [MockSection("Movies"), MockSection("TV Shows")]
            def __init__(self, *args, **kwargs):
                self.library = self.MockLibrary()
        return MockServer(url, token)
        
    monkeypatch.setattr(server, "PlexServer", mock_plex_server)
    
    # Test POST request to validation endpoint
    payload = {
        "ollama_api_url": "http://mock-ollama:11434",
        "ollama_model": "qwen2.5-vl",
        "scan_paths": "./media_mock",
        "plex_url": "http://mock-plex:32400",
        "plex_token": "mock-token"
    }
    
    # Create temp directory structure to test scan paths
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp_dir:
        mock_path = Path(tmp_dir) / "media_mock"
        mock_path.mkdir()
        payload["scan_paths"] = str(mock_path)
        
        response = client.post("/api/pipeline/config/test", json=payload)
        assert response.status_code == 200
        data = response.json()
        
        # Verify Ollama check succeeded
        assert data["ollama"]["success"] is True
        assert "Connected successfully" in data["ollama"]["message"]
        
        # Verify Plex check succeeded
        assert data["plex"]["success"] is True
        assert "Movies" in data["plex"]["message"]
        
        # Verify scan path checked and accessible
        assert len(data["paths"]) == 1
        assert data["paths"][0]["path"] == str(mock_path)
        assert data["paths"][0]["success"] is True

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
    
    cmd = ["sleep", "10"]
    
    with pytest.raises(TimeoutError) as exc_info:
        run_throttled_process(cmd, timeout=1)
        
    assert "Process timed out after 1 seconds" in str(exc_info.value)

def test_extended_audit_endpoints_and_worker():
    from app.database import AuditJob, JobStatus, FileStatus, AuditResult
    
    db = TestingSessionLocal()
    # Clean previous records
    db.query(AuditJob).delete()
    db.query(MediaFile).delete()
    db.query(AuditResult).delete()
    db.commit()
    
    # Create file
    media_file = MediaFile(
        filepath="/media/TV/Show/S01E01.mkv",
        filename="S01E01.mkv",
        status=FileStatus.VERIFIED
    )
    db.add(media_file)
    db.commit()
    file_id = media_file.id
    db.close()
    
    # 1. POST to trigger extended audit
    response = client.post(f"/api/files/{file_id}/extended-audit")
    assert response.status_code == 200
    assert "successfully queued" in response.json()["message"]
    
    # Verify file and job states in DB
    db_verify = TestingSessionLocal()
    f_rec = db_verify.query(MediaFile).filter(MediaFile.id == file_id).first()
    j_rec = db_verify.query(AuditJob).filter(AuditJob.media_file_id == file_id).first()
    assert f_rec.status == FileStatus.PENDING
    assert j_rec.is_extended is True
    assert j_rec.status == JobStatus.PENDING
    db_verify.close()
    
    # 2. POST again (should fail with 400 because a job is already active)
    response_dup = client.post(f"/api/files/{file_id}/extended-audit")
    assert response_dup.status_code == 400
    assert "already pending or processing" in response_dup.json()["detail"]
    
    # 3. Insert a mock AuditResult with extended audit details
    db_result = TestingSessionLocal()
    res = AuditResult(
        media_file_id=file_id,
        status=FileStatus.VERIFIED,
        is_extended_audit=True,
        extended_audit_passed=True,
        extended_audit_notes="Mock dialogue matched perfectly with overview.",
        confidence_score=95
    )
    db_result.add(res)
    db_result.commit()
    db_result.close()
    
    # 4. Query GET /api/files and verify details are returned
    response_get = client.get("/api/files")
    assert response_get.status_code == 200
    data = response_get.json()
    assert data["total_items"] == 1
    item = data["items"][0]
    assert item["audit_result"]["is_extended_audit"] is True
    assert item["audit_result"]["extended_audit_passed"] is True
    assert item["audit_result"]["extended_audit_notes"] == "Mock dialogue matched perfectly with overview."

def test_get_files_sorting_and_source_path_filtering():
    # Setup test database connection
    from app.database import MediaFile, AuditResult
    db = TestingSessionLocal()
    
    # Clean previous records for isolated check
    db.query(AuditResult).delete()
    db.query(MediaFile).delete()
    db.commit()
    
    # Create movies and tv shows records
    m1 = MediaFile(filepath="/media/Movies/A_Movie.mkv", filename="A_Movie.mkv", media_type="movie", status=FileStatus.PENDING, plex_rating_key="12345")
    m2 = MediaFile(filepath="/media/Movies/B_Movie.mkv", filename="B_Movie.mkv", media_type="movie", status=FileStatus.VERIFIED)
    t1 = MediaFile(filepath="/media/TV/Show_A.mkv", filename="Show_A.mkv", media_type="episode", status=FileStatus.PENDING)
    
    db.add_all([m1, m2, t1])
    db.commit()
    
    # Add SystemConfig parameters for Plex
    from app.database import SystemConfig
    db.add(SystemConfig(key="PLEX_URL", value="http://10.0.0.54:32400"))
    db.add(SystemConfig(key="PLEX_MACHINE_IDENTIFIER", value="mock_machine_id"))
    db.commit()
    
    # Add audit results for confidence/date sorting tests
    r1 = AuditResult(media_file_id=m1.id, confidence_score=80, status=FileStatus.VERIFIED)
    r2 = AuditResult(media_file_id=m2.id, confidence_score=95, status=FileStatus.VERIFIED)
    
    db.add_all([r1, r2])
    db.commit()
    
    db.close()
    
    # 1. Test filtering by source_path=movies
    resp = client.get("/api/files?source_path=movies")
    assert resp.status_code == 200
    assert resp.json()["total_items"] == 2
    items = resp.json()["items"]
    m1_item = next(i for i in items if i["filename"] == "A_Movie.mkv")
    assert m1_item["plex_play_url"].endswith("/web/index.html#!/server/mock_machine_id/details?key=%2Flibrary%2Fmetadata%2F12345")
    
    # 2. Test filtering by source_path=tv
    resp = client.get("/api/files?source_path=tv")
    assert resp.status_code == 200
    assert resp.json()["total_items"] == 1
    assert resp.json()["items"][0]["filename"] == "Show_A.mkv"
    
    # 3. Test sorting by filename asc
    resp = client.get("/api/files?sort_by=filename&sort_order=asc")
    filenames = [item["filename"] for item in resp.json()["items"]]
    assert filenames == ["A_Movie.mkv", "B_Movie.mkv", "Show_A.mkv"]
    
    # 4. Test sorting by confidence score desc
    resp = client.get("/api/files?sort_by=confidence&sort_order=desc")
    # m2 has 95, m1 has 80, t1 has none
    assert resp.json()["items"][0]["filename"] == "B_Movie.mkv"
    assert resp.json()["items"][1]["filename"] == "A_Movie.mkv"

def test_export_files():
    from app.database import MediaFile, AuditResult
    db = TestingSessionLocal()
    
    # Clean previous records
    db.query(AuditResult).delete()
    db.query(MediaFile).delete()
    db.commit()
    
    # Create movies records
    m1 = MediaFile(filepath="/media/Movies/A_Movie.mkv", filename="A_Movie.mkv", media_type="movie", status=FileStatus.PENDING)
    m2 = MediaFile(filepath="/media/Movies/B_Movie.mkv", filename="B_Movie.mkv", media_type="movie", status=FileStatus.VERIFIED)
    
    db.add_all([m1, m2])
    db.commit()
    db.close()

    # Verify export endpoint generates correct headers and JSON contents
    resp = client.get("/api/files/export?source_path=movies&sort_by=filename&sort_order=asc")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert "attachment; filename=plex_audit_snapshot_" in resp.headers["content-disposition"]
    
    data = resp.json()
    assert len(data) == 2
    assert data[0]["filename"] == "A_Movie.mkv"
    assert data[1]["filename"] == "B_Movie.mkv"

def test_requeue_all_flagged():
    from app.database import MediaFile, AuditResult
    db = TestingSessionLocal()
    
    # Clean previous records
    db.query(AuditResult).delete()
    db.query(MediaFile).delete()
    db.commit()
    
    # Create multiple flagged files with different statuses
    f1 = MediaFile(filepath="/media/Movies/A.mkv", filename="A.mkv", status=FileStatus.FLAGGED_TITLE)
    f2 = MediaFile(filepath="/media/Movies/B.mkv", filename="B.mkv", status=FileStatus.FLAGGED_DURATION)
    f3 = MediaFile(filepath="/media/Movies/C.mkv", filename="C.mkv", status=FileStatus.VERIFIED)
    
    db.add_all([f1, f2, f3])
    db.commit()
    db.close()
    
    # Bulk requeue FLAGGED
    resp = client.post("/api/pipeline/requeue-by-status?status=FLAGGED")
    assert resp.status_code == 200
    assert "Successfully requeued 2 files" in resp.json()["message"]
    
    # Verify the flagged files are now PENDING, and VERIFIED was untouched
    db_verify = TestingSessionLocal()
    pending_files = db_verify.query(MediaFile).filter(MediaFile.status == FileStatus.PENDING).all()
    verified_files = db_verify.query(MediaFile).filter(MediaFile.status == FileStatus.VERIFIED).all()
    assert len(pending_files) == 2
    assert len(verified_files) == 1
    db_verify.close()

def test_enrich_plex_metadata(monkeypatch):
    from app.database import MediaFile, FileStatus
    from app.plex_client import PlexClient
    
    def mock_get_all_paths_mapping(self):
        return {
            "a.mkv": (7200.0, "rating_111"),
            "movies/b.mkv": (5400.0, "rating_222")
        }
        
    monkeypatch.setattr(PlexClient, "get_all_paths_mapping", mock_get_all_paths_mapping)
    monkeypatch.setattr(PlexClient, "_get_path_suffix", lambda self, fp: fp.lower().replace("\\", "/"))

    db = TestingSessionLocal()
    db.query(MediaFile).delete()
    db.commit()
    
    # f1 directory path is completely different from the plex key, so only filename match applies
    f1 = MediaFile(filepath="/different/mount/path/movies/a.mkv", filename="a.mkv", status=FileStatus.PENDING, expected_duration=None, plex_rating_key=None)
    f2 = MediaFile(filepath="movies/b.mkv", filename="b.mkv", status=FileStatus.PENDING, expected_duration=100.0, plex_rating_key="old_key")
    db.add_all([f1, f2])
    db.commit()
    db.close()
    
    resp = client.post("/api/pipeline/enrich-plex")
    assert resp.status_code == 200
    res_json = resp.json()
    assert "Updated 2 files" in res_json["message"]
    assert res_json["diagnostics"]["updated_count"] == 2
    assert res_json["diagnostics"]["matched_count"] == 2
    
    db_verify = TestingSessionLocal()
    f1_db = db_verify.query(MediaFile).filter(MediaFile.filename == "a.mkv").first()
    f2_db = db_verify.query(MediaFile).filter(MediaFile.filename == "b.mkv").first()
    assert f1_db.expected_duration == 7200.0
    assert f1_db.plex_rating_key == "rating_111"
    assert f2_db.expected_duration == 5400.0
    assert f2_db.plex_rating_key == "rating_222"
    db_verify.close()

def test_delete_file(tmp_path):
    from app.database import MediaFile, AuditJob, AuditResult, JobStatus, FileStatus
    import os
    
    db = TestingSessionLocal()
    db.query(AuditJob).delete()
    db.query(AuditResult).delete()
    db.query(MediaFile).delete()
    db.commit()
    
    # Create a dummy physical file
    dummy_file = tmp_path / "corrupt_movie.mkv"
    dummy_file.write_text("dummy binary data")
    
    f1 = MediaFile(
        filepath=str(dummy_file),
        filename="corrupt_movie.mkv",
        status=FileStatus.FLAGGED_CORRUPT
    )
    db.add(f1)
    db.commit()
    file_id = f1.id
    
    # Add pending job and audit result
    job = AuditJob(media_file_id=file_id, status=JobStatus.PENDING)
    res = AuditResult(media_file_id=file_id, status=FileStatus.FLAGGED_CORRUPT)
    db.add_all([job, res])
    db.commit()
    db.close()
    
    # Assert physical file exists
    assert os.path.exists(str(dummy_file))
    
    # Delete API request
    resp = client.delete(f"/api/files/{file_id}")
    assert resp.status_code == 200
    assert "Successfully deleted" in resp.json()["message"]
    
    # Assert physical file was unlinked
    assert not os.path.exists(str(dummy_file))
    
    # Assert database records are gone
    db_verify = TestingSessionLocal()
    assert db_verify.query(MediaFile).filter(MediaFile.id == file_id).first() is None
    assert db_verify.query(AuditJob).filter(AuditJob.media_file_id == file_id).first() is None
    assert db_verify.query(AuditResult).filter(AuditResult.media_file_id == file_id).first() is None
    db_verify.close()
