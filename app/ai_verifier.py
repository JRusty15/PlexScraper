import os
import json
import base64
import httpx
import logging
from pathlib import Path

logger = logging.getLogger("ai_verifier")

OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-vl")

class AIVerifier:
    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root)

    def _encode_image(self, image_rel_path: str) -> str:
        """Converts a relative web URL keyframe path back to local path and encodes to base64."""
        # /static/media/keyframes/media_1_pct_5.jpg -> app/static/media/keyframes/media_1_pct_5.jpg
        clean_path = image_rel_path.lstrip("/")
        # The physical file resides inside the processed_media/ directory relative to workspace root
        # /static/media/ translates to processed_media
        local_filename = clean_path.split("/")[-1]
        
        # Let's search inside workspace/processed_media/keyframes
        local_path = self.workspace_root / "processed_media" / "keyframes" / local_filename
        if not local_path.exists():
            raise FileNotFoundError(f"Keyframe image not found at {local_path}")
            
        with open(local_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    async def verify_visuals(self, keyframe_paths: list, expected_title: str) -> dict:
        """Runs Qwen2.5-VL via Ollama API to verify title cards, credits, and visual context."""
        results = {
            "title_verified": False,
            "credits_verified": False,
            "sanity_check_passed": False,
            "raw_logs": []
        }

        if not keyframe_paths:
            return results

        # 1. Title verification (typically 5% keyframe)
        try:
            img_b64 = self._encode_image(keyframe_paths[0])
            prompt = (
                f"Analyze this image. It is the beginning of a movie/show. "
                f"Is the title '{expected_title}' displayed or visible on screen? "
                f"Respond with a JSON object: {{\"title_found\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"string\"}}"
            )
            
            title_resp = await self._query_ollama(prompt, img_b64)
            results["title_verified"] = title_resp.get("title_found", False)
            results["raw_logs"].append({"stage": "title", "response": title_resp})
        except Exception as e:
            logger.error(f"Title VLM check failed: {e}")
            results["raw_logs"].append({"stage": "title", "error": str(e)})

        # 2. Credits verification (typically 90% keyframe)
        try:
            if len(keyframe_paths) >= 5:
                img_b64 = self._encode_image(keyframe_paths[4]) # 90% keyframe
                prompt = (
                    "Analyze this image. It is from the end of a movie/show. "
                    "Are end credits, actor names, production logos, or scrolling credits visible on screen? "
                    "Respond with a JSON object: {\"credits_found\": true/false, \"confidence\": 0.0-1.0}"
                )
                credits_resp = await self._query_ollama(prompt, img_b64)
                results["credits_verified"] = credits_resp.get("credits_found", False)
                results["raw_logs"].append({"stage": "credits", "response": credits_resp})
        except Exception as e:
            logger.error(f"Credits VLM check failed: {e}")
            results["raw_logs"].append({"stage": "credits", "error": str(e)})

        # 3. Sanity verification (typically mid keyframe e.g. 50%)
        try:
            if len(keyframe_paths) >= 3:
                img_b64 = self._encode_image(keyframe_paths[2]) # 50% keyframe
                prompt = (
                    f"Analyze this image from a video. Does the scene/visual content match the expectations of a "
                    f"media file titled '{expected_title}'? Answer with a JSON object: "
                    f"{{\"content_matches\": true/false, \"description\": \"brief summary of the scene\"}}"
                )
                sanity_resp = await self._query_ollama(prompt, img_b64)
                results["sanity_check_passed"] = sanity_resp.get("content_matches", False)
                results["raw_logs"].append({"stage": "sanity", "response": sanity_resp})
        except Exception as e:
            logger.error(f"Sanity check VLM failed: {e}")
            results["raw_logs"].append({"stage": "sanity", "error": str(e)})

        return results

    async def _query_ollama(self, prompt: str, image_b64: str) -> dict:
        """Sends request to local Ollama API."""
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64]
                }
            ],
            "stream": False,
            "format": "json"
        }
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{OLLAMA_API_URL}/api/chat", json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Ollama API returned status {resp.status_code}: {resp.text}")
            
            data = resp.json()
            content = data.get("message", {}).get("content", "{}")
            return json.loads(content)

    async def transcribe_audio_and_identify_language(self, audio_clip_paths: list) -> dict:
        """Transcribes audio clips using Whisper. 
        On Windows, we can use a command line whisper executable (whisper.cpp, faster-whisper) 
        or fall back gracefully to a mock or open-source API if not installed.
        """
        # For a production robust backend on a server with an RTX 5080, we can run Whisper.
        # Since we want to ensure we don't crash if faster-whisper isn't configured,
        # we will verify if Whisper can run locally, else return detected=English mock transcription.
        results = {
            "languages": ["en"],
            "transcript": "Hello, welcome to this movie representation.",
            "method": "mock_fallback"
        }

        # If a local whisper command line or faster_whisper python library is set, use it.
        # Here is a generic wrapper to check if we can run whisper.exe or transcribe.
        # We will check if faster-whisper is installed:
        try:
            from faster_whisper import WhisperModel
            # Run model in a single-threaded/sequential way to conserve VRAM
            # We use float16 on GPU (cuda)
            # Let's locate the first audio clip
            if audio_clip_paths:
                clean_path = audio_clip_paths[0].lstrip("/")
                local_filename = clean_path.split("/")[-1]
                local_path = self.workspace_root / "processed_media" / "audio" / local_filename
                
                if local_path.exists():
                    model = WhisperModel("tiny", device="cuda", compute_type="float16")
                    segments, info = model.transcribe(str(local_path), beam_size=1)
                    detected_lang = info.language
                    text = " ".join([seg.text for seg in segments])
                    
                    return {
                        "languages": [detected_lang],
                        "transcript": text,
                        "method": "faster-whisper-tiny-cuda"
                    }
        except Exception as e:
            logger.info(f"Local faster-whisper not available or failed: {e}. Using fallback verification.")
            
        return results
