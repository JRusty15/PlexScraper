import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app, get_db
from app.database import Base, MediaFile, FileStatus

# Setup in-memory test database
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
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
    yield
    Base.metadata.drop_all(bind=engine)

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
