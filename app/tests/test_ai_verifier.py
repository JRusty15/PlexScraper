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
        "The.Big.Bang.Theory.S10E23.HDTV.x264-SVA": "The Big Bang Theory"
    }
    
    for raw, expected in test_cases.items():
        assert verifier._sanitize_title(raw) == expected
