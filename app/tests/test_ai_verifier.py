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
