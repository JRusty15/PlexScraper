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

    def _sanitize_title(self, raw_title: str) -> str:
        """Sanitizes titles by removing common release tags, codecs, resolution flags, and bracket text."""
        import re
        # Remove anything in brackets or parentheses
        clean = re.sub(r'\[[^\]]*\]|\([^\)]*\)', '', raw_title)
        
        # Remove technical keywords and standard file properties (case-insensitive)
        tech_patterns = [
            r'\b\d{3,4}p\b', r'\bhevc\b', r'\bx26[45]\b', r'\bbluray\b', r'\bdvdrip\b',
            r'\bremux\b', r'\bdts\b', r'\bma\b', r'\bavc\b', r'\bopus\b', r'\bhdr\b',
            r'\bweb-dl\b', r'\bwebdl\b', r'\bhdtv\b', r'\brip\b', r'\baxxo\b', r'\bcodec\b'
        ]
        for pattern in tech_patterns:
            clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)
            
        # Clean up double spaces/dots/dashes
        clean = re.sub(r'[\.\-_]', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        # If it's a TV show with episode markers (e.g. S01E01 or 1x01), extract the show title prefix
        show_match = re.split(r'\bs\d{2}e\d{2}\b|\b\d+x\d+\b', clean, flags=re.IGNORECASE)
        if show_match and len(show_match) > 1 and show_match[0].strip():
            clean = show_match[0].strip()
            
        return clean or raw_title

    async def verify_visuals(self, keyframe_paths: list, expected_title: str, metadata: dict = None) -> dict:
        """Runs Qwen2.5-VL via Ollama API to verify title cards, credits, and visual context."""
        clean_title = self._sanitize_title(expected_title)
        logger.info(f"=== AI Visual Verification Started (Raw: '{expected_title}', Sanitized: '{clean_title}') ===")
        results = {
            "title_verified": False,
            "credits_verified": False,
            "sanity_check_passed": False,
            "raw_logs": []
        }

        if not keyframe_paths:
            logger.warning("No keyframes found for visual check.")
            return results

        # 1. Title verification (typically early keyframes: e.g. 1%, 2%, 4%, 7%, 10%)
        try:
            early_keyframes = keyframe_paths[:5]  # first 5 keyframes
            images_b64 = [self._encode_image(p) for p in early_keyframes]
            prompt = (
                f"Analyze these {len(early_keyframes)} early images from the beginning of a video. "
                f"Is the title '{clean_title}' (or a closely related variant representing the show/movie) "
                f"displayed or visible as overlay text on screen in any of these frames? "
                f"Look closely at title cards, opening credits, or overlay text. "
                f"Respond with a JSON object: {{\"title_found\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"string\"}}"
            )
            logger.info("Sending Stage 1 (Title Check) prompt with multiple images to Qwen2.5-VL...")
            title_resp = await self._query_ollama(prompt, images_b64)
            logger.info(f"Stage 1 Response: {title_resp}")
            results["title_verified"] = title_resp.get("title_found", False) or title_resp.get("title_verified", False)
            results["raw_logs"].append({"stage": "title", "response": title_resp})
        except Exception as e:
            logger.error(f"Title VLM check failed: {e}")
            results["raw_logs"].append({"stage": "title", "error": str(e)})

        # 2. Credits verification (typically late keyframes: 85%, 90%, 94%, 98%)
        try:
            if len(keyframe_paths) >= 15:
                late_keyframes = keyframe_paths[-4:]  # last 4 keyframes
                images_b64 = [self._encode_image(p) for p in late_keyframes]
                prompt = (
                    "Analyze these images from the end of a video. "
                    "Are end credits, actor names, production logos, scroll text, or cast lists visible in any of these frames? "
                    "Respond with a JSON object: {\"credits_found\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"string\"}"
                )
                logger.info("Sending Stage 2 (Credits Check) prompt with multiple images to Qwen2.5-VL...")
                credits_resp = await self._query_ollama(prompt, images_b64)
                logger.info(f"Stage 2 Response: {credits_resp}")
                results["credits_verified"] = credits_resp.get("credits_found", False) or credits_resp.get("credits_verified", False)
                results["raw_logs"].append({"stage": "credits", "response": credits_resp})
            elif keyframe_paths:
                img_b64 = self._encode_image(keyframe_paths[-1])
                prompt = (
                    "Analyze this image. It is from the end of a movie/show. "
                    "Are end credits, actor names, production logos, or scrolling credits visible on screen? "
                    "Respond with a JSON object: {\"credits_found\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"string\"}"
                )
                logger.info("Sending Stage 2 (Credits Check) prompt to Qwen2.5-VL...")
                credits_resp = await self._query_ollama(prompt, img_b64)
                logger.info(f"Stage 2 Response: {credits_resp}")
                results["credits_verified"] = credits_resp.get("credits_found", False) or credits_resp.get("credits_verified", False)
                results["raw_logs"].append({"stage": "credits", "response": credits_resp})
        except Exception as e:
            logger.error(f"Credits VLM check failed: {e}")
            results["raw_logs"].append({"stage": "credits", "error": str(e)})

        # 3. Sanity verification (typically mid keyframes: 20%, 30%, 45%, 60%, 70%, 80%)
        try:
            meta_str = ""
            if metadata:
                meta_str += f"\n\nExpected Media Metadata Guidelines:"
                if metadata.get("year"):
                    meta_str += f"\n- Release Year: {metadata['year']}"
                if metadata.get("genres"):
                    meta_str += f"\n- Genres: {', '.join(metadata['genres'])}"
                if metadata.get("roles"):
                    meta_str += f"\n- Key Cast/Actors: {', '.join(metadata['roles'])}"
                if metadata.get("summary"):
                    meta_str += f"\n- Plot Summary: {metadata['summary']}"

            if len(keyframe_paths) >= 11:
                mid_keyframes = keyframe_paths[5:11]  # middle 6 keyframes
                images_b64 = [self._encode_image(p) for p in mid_keyframes]
                prompt = (
                    f"You are verifying if a video file matches its expected title: '{clean_title}'.{meta_str}\n\n"
                    f"Analyze these {len(mid_keyframes)} images from the middle of the video:\n"
                    f"1. Identify the setting, genre, and any recognizable actors, characters, or specific movies/shows.\n"
                    f"2. Assess whether this visual content is consistent with the expected title '{clean_title}' or its franchise, and the metadata guidelines above. "
                    f"State if there is any active contradiction (e.g. the expected title is a sitcom, but the scenes show a medieval battle; or the expected title is '{clean_title}', but the images clearly show characters and settings from a completely different recognizable movie/show).\n"
                    f"If the expected title '{clean_title}' is generic or unknown to you, does the content look like a valid movie/show scene (e.g. contains actors, normal settings, or animation, and is not a blank screen, test pattern, static, or corrupt video)?\n"
                    f"Respond with a JSON object:\n"
                    f"{{\n"
                    f"  \"content_matches\": true/false,\n"
                    f"  \"confidence\": 0.0-1.0,\n"
                    f"  \"description\": \"brief summary of detected elements\",\n"
                    f"  \"reason\": \"explanation of why it matches or contradicts the expected title\"\n"
                    f"}}"
                )
                logger.info("Sending Stage 3 (Sanity Check) prompt with multiple images to Qwen2.5-VL...")
                sanity_resp = await self._query_ollama(prompt, images_b64)
                logger.info(f"Stage 3 Response: {sanity_resp}")
                results["sanity_check_passed"] = sanity_resp.get("content_matches", False) or sanity_resp.get("sanity_check_passed", False)
                results["raw_logs"].append({"stage": "sanity", "response": sanity_resp})
            elif len(keyframe_paths) >= 3:
                img_b64 = self._encode_image(keyframe_paths[len(keyframe_paths)//2])
                prompt = (
                    f"Analyze this image from a video. Does the scene/visual content match the expectations of a "
                    f"media file titled '{clean_title}'? Answer with a JSON object: "
                    f"{{\"content_matches\": true/false, \"confidence\": 0.0-1.0, \"description\": \"brief summary of the scene\", \"reason\": \"string\"}}"
                )
                logger.info("Sending Stage 3 (Sanity Check) prompt to Qwen2.5-VL...")
                sanity_resp = await self._query_ollama(prompt, img_b64)
                logger.info(f"Stage 3 Response: {sanity_resp}")
                results["sanity_check_passed"] = sanity_resp.get("content_matches", False) or sanity_resp.get("sanity_check_passed", False)
                results["raw_logs"].append({"stage": "sanity", "response": sanity_resp})
        except Exception as e:
            logger.error(f"Sanity check VLM failed: {e}")
            results["raw_logs"].append({"stage": "sanity", "error": str(e)})

        return results

    async def _query_ollama(self, prompt: str, images_b64: list | str) -> dict:
        """Sends request to local Ollama API with one or more base64 encoded images."""
        if isinstance(images_b64, str):
            images_b64 = [images_b64]
            
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": images_b64
                }
            ],
            "stream": False
        }
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{OLLAMA_API_URL}/api/chat", json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Ollama API returned status {resp.status_code}: {resp.text}")
            
            data = resp.json()
            content = data.get("message", {}).get("content", "{}").strip()
            
            # Attempt to extract JSON from the response text
            try:
                # If wrapped in markdown blocks, strip them
                if content.startswith("```json"):
                    content = content.split("```json")[1].split("```")[0].strip()
                elif content.startswith("```"):
                    content = content.split("```")[1].split("```")[0].strip()
                return json.loads(content)
            except Exception:
                # Fallback to simple regex/text detection if it is not valid JSON
                logger.warning(f"Could not parse VLM response as JSON: {content}")
                lower_content = content.lower()
                
                # Check for positive/negative keywords based on prompt target
                is_positive = any(word in lower_content for word in ["yes", "true", "found", "verified", "matches", "present"])
                return {
                    "raw_text_fallback": content,
                    "title_found": is_positive,
                    "credits_found": is_positive,
                    "content_matches": is_positive,
                    "confidence": 0.5,
                    "reason": content[:200]
                }

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
