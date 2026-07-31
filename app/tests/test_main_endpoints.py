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
