import pytest
from app.ai_verifier import AIVerifier

def test_sanitize_title():
    verifier = AIVerifier(workspace_root=".")
    
    # Test cases mapping raw titles to expected sanitized outputs
    test_cases = {
        "Mad Men - S03E03 - My Old Kentucky Home Bluray-720p": "Mad Men",
        "The Dark Knight 1080p x264 Remux DTS-MA": "The Dark Knight",
        "Interstellar [HEVC 2160p HDR AV1]": "Interstellar",
        "Breaking Bad S01E01 Pilot WEB-DL x265": "Breaking Bad",
        "Inception.2010.1080p.BluRay.x264-FGT": "Inception 2010",
        "Melissa & Joey - S01E09 - Seoul Man WEBDL-1080p": "Melissa & Joey",
        "The.Simpsons.S30E05.720p.HDTV.x264": "The Simpsons",
        "the.big.bang.theory.1011.hdtv-lol": "the big bang theory",
        "The.Big.Bang.Theory.S10E23.HDTV.x264-SVA": "The Big Bang Theory",
        "Need.for.Speed.2014.3D.PROPER.1080p.BluRay.x264-GLASSES": "Need for Speed 2014",
        "Bad.Neighbors.2014.WEBRip.HC.XviD.MP3-RARBG": "Bad Neighbors 2014",
        "Night.at.the.Museum.Battle.of.the.Smithsonian.2009.Proper.1080p.BRRip.DDP.5.1.H.265.-iVy": "Night at the Museum Battle of the Smithsonian 2009",
        "Next.Day.Air.2009.1080p.FDNG.WEB-DL.DDP.5.1.H.264-PiRaTeS": "Next Day Air 2009"
    }
    
    for raw, expected in test_cases.items():
        assert verifier._sanitize_title(raw) == expected


def test_as_bool():
    verifier = AIVerifier(workspace_root=".")
    # Booleans
    assert verifier._as_bool(True) is True
    assert verifier._as_bool(False) is False
    
    # Truthy Strings
    assert verifier._as_bool("true") is True
    assert verifier._as_bool("True") is True
    assert verifier._as_bool("yes") is True
    assert verifier._as_bool("1") is True
    assert verifier._as_bool("y") is True
    assert verifier._as_bool("T") is True
    
    # Falsy Strings
    assert verifier._as_bool("false") is False
    assert verifier._as_bool("False") is False
    assert verifier._as_bool("no") is False
    assert verifier._as_bool("0") is False
    assert verifier._as_bool("other") is False
    
    # Numbers
    assert verifier._as_bool(1) is True
    assert verifier._as_bool(0) is False
    assert verifier._as_bool(0.0) is False
    
    # None / Empty
    assert verifier._as_bool(None) is False


@pytest.mark.asyncio
async def test_query_ollama_json_extraction(monkeypatch):
    import httpx
    verifier = AIVerifier(workspace_root=".")
    
    # Mock successful JSON with conversation prefix/suffix
    class MockResponse:
        def __init__(self, text):
            self.status_code = 200
            self.text = text
        def json(self):
            return {"message": {"content": self.text}}
            
    async def mock_post(self, url, json):
        # We return a content with prefix, wrapped in codeblocks
        raw_content = "Analysis: ```json\n{\n  \"content_matches\": \"false\",\n  \"confidence\": 0.9\n}\n```\nHope that helps!"
        return MockResponse(raw_content)
        
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    
    # Trigger query (the prompt and images can be dummy values)
    res = await verifier._query_ollama("dummy prompt", "dummy_image")
    
    # Assert JSON was successfully extracted and parsed
    assert res.get("content_matches") == "false"
    assert res.get("confidence") == 0.9


@pytest.mark.asyncio
async def test_query_ollama_fallback_parser(monkeypatch):
    import httpx
    verifier = AIVerifier(workspace_root=".")
    
    class MockResponse:
        def __init__(self, text):
            self.status_code = 200
            self.text = text
        def json(self):
            return {"message": {"content": self.text}}
            
    # Case 1: Plain text with negative matches and keywords (e.g. emergency responder, not related to Bobs Burgers)
    async def mock_post_negative(self, url, json):
        return MockResponse("This scene is a live action drama and not related to Bob's Burgers. The content_matches is false.")
        
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_negative)
    res_neg = await verifier._query_ollama("dummy prompt", "dummy_image")
    
    # Assert fallback correctly identifies negation / negatives
    assert res_neg["content_matches"] is False
    assert res_neg["title_found"] is False
    
    # Case 2: Plain text with positive statements (e.g. yes, this matches the expected show and is verified.)
    async def mock_post_positive(self, url, json):
        return MockResponse("yes, this matches the expected show and is verified.")
        
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_positive)
    res_pos = await verifier._query_ollama("dummy prompt", "dummy_image")
    
    assert res_pos["content_matches"] is True
    assert res_pos["title_found"] is True

