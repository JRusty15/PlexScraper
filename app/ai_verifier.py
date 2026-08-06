import os
import json
import base64
import httpx
import logging
from pathlib import Path
from app.database import get_system_config

logger = logging.getLogger("ai_verifier")

class AIVerifier:
    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root)

    def _encode_image(self, image_rel_path: str) -> str:
        """Converts a relative web URL keyframe path back to local path and encodes to base64."""
        clean_path = image_rel_path.lstrip("/")
        local_filename = clean_path.split("/")[-1]
        local_path = self.workspace_root / "processed_media" / "keyframes" / local_filename
        if not local_path.exists():
            raise FileNotFoundError(f"Keyframe image not found at {local_path}")
            
        with open(local_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")

    def _sanitize_title(self, raw_title: str) -> str:
        """Sanitizes titles by removing file extensions, common release tags, codecs, resolution flags, and bracket text."""
        import re
        
        # Remove file extension if present (e.g. .mkv, .mp4)
        clean = re.sub(r'\.[a-zA-Z0-9]{3,4}$', '', raw_title)
        
        # Remove trailing release group suffixes (e.g., -BEN.THE.MEN, -GLASSES, -iVy) at the end of the filename stem
        clean = re.sub(r'-[a-zA-Z0-9\.\-_]+$', '', clean)
        
        # Remove anything in brackets or parentheses
        clean = re.sub(r'\[[^\]]*\]|\([^\)]*\)', '', clean)
        
        # Case insensitive strip of standard release groups, tags, and qualities
        tags = [
            # Resolutions
            r'\b\d{3,4}p\b',
            # Sources / Quality
            r'\b(?:bluray|web[\.\-\_]?dl|webrip|brrip|hdrip|dvdrip|hdtv|bdrip|remux|axxo|rip|codec|web)\b',
            r'\b(?:proper|repack|hc|hdr|dv|3d|10bit|unrated|extended|limited|multi|sub(s)?|dual|atmos|hdr10|hdr10plus|dolby[\.\-\_]?vision)\b',
            r'\bdirector(s)?\s+cut\b',
            # Codecs / Containers
            r'\b(?:x264|x265|h[\.\-\_]?26[45]|hevc|avc|xvid|divx|opus|mp4|mkv|avi|m4v|mov)\b',
            # Audio (including DTS-HD, DTS-MA, Dolby Digital, etc.)
            r'\b(?:dts[\.\-\_]?(?:hd|ma)?|truehd|ddp|dd\d\.\d|ddp\d\.\d|dd|aac|mp3|eac3|flac|5\s*\.\s*1|2\s*\.\s*0)\b',
            # Release groups / Scene keywords (common list)
            r'\b(?:fgt|rarbg|yts|esub|psa|glasses|evo|amiable|ivy|oft|framestor|pirates|fdng|kralimarko|ptp|alfahd|postbot)\b'
        ]
        
        for tag_regex in tags:
            clean = re.sub(tag_regex, ' ', clean, flags=re.IGNORECASE)
            
        # Replace common delimiter patterns with spaces
        clean = re.sub(r'[\.\-\_\+]', ' ', clean)
        
        # Clean extra whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        # If it's a TV show with episode markers or 3-4 digit season/episode numbers (e.g., 1011, 1007, S12E10, S10E23, 12x04)
        # We split by these markers and keep only the preceding show name prefix.
        # We use a negative lookahead to prevent stripping movie release years (e.g. 1930-2029)
        show_match = re.split(r'\bs\d{1,2}e\d{1,2}\b|\b\d+x\d+\b|\b(?!(?:19[3-9]\d|20[0-2]\d)\b)\d{3,4}\b', clean, flags=re.IGNORECASE)
        if show_match and len(show_match) > 0 and show_match[0].strip():
            clean = show_match[0].strip()
            
        return clean or raw_title

    async def verify_visuals(self, keyframe_paths: list, expected_title: str, metadata: dict = None, filepath: str = None) -> dict:
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

        # Determine the best title for title card check (Show Title for TV episodes, Movie Title for movies)
        show_title = (metadata or {}).get("show_title")
        episode_title = (metadata or {}).get("title") if show_title else None
        
        if show_title and episode_title:
            search_titles = f"'{show_title}' or the episode title '{episode_title}'"
        else:
            search_titles = f"'{clean_title}'"

        # 1. Title verification (typically early keyframes: e.g. 1%, 2%, 4%, 7%, 10%)
        try:
            early_keyframes = keyframe_paths[:5]  # first 5 keyframes
            images_b64 = [self._encode_image(p) for p in early_keyframes]
            prompt = (
                f"Analyze these {len(early_keyframes)} early images from the beginning of a video. "
                f"Is the title {search_titles} (or a closely related variant representing the show/movie) "
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
            is_animated = False
            if metadata:
                meta_str += f"\n\nExpected Media Metadata Guidelines:"
                if metadata.get("year"):
                    meta_str += f"\n- Release Year: {metadata['year']}"
                if metadata.get("genres"):
                    meta_str += f"\n- Genres: {', '.join(metadata['genres'])}"
                    genres_lower = [g.lower() for g in metadata["genres"]]
                    if "animation" in genres_lower or "animated" in genres_lower:
                        is_animated = True
                if metadata.get("roles"):
                    meta_str += f"\n- Key Cast/Actors: {', '.join(metadata['roles'])}"
                if metadata.get("summary"):
                    meta_str += f"\n- Plot Summary: {metadata['summary']}"

            # Check if animated via filename / filepath keywords
            title_lower = clean_title.lower()
            path_lower = filepath.lower() if filepath else ""
            animated_keywords = [
                "simpsons", "futurama", "family guy", "american dad", "robot chicken", 
                "ren and stimpy", "ren & stimpy", "ariel", "arthur", "bunnicula", 
                "daniel tiger", "duckman", "garfield", "reboot", "rick and morty", 
                "rick & morty", "trash truck", "looney tunes", "mickey mouse", "cartoon",
                "animated", "animation"
            ]
            if any(kw in title_lower or kw in path_lower for kw in animated_keywords):
                is_animated = True

            # Set visual context guidelines dynamically based on genre
            if is_animated:
                genre_context = (
                    f"The expected show/movie '{clean_title}' is an ANIMATED cartoon or CGI movie. "
                    "Note that modern 3D CGI animation (like Pixar, Illumination, DreamWorks) often features extremely "
                    "realistic, near-photorealistic backgrounds, physics, water, and environments (such as supermarkets, "
                    "beaches, streets, or grocery store shelves). Do NOT reject the file if the backgrounds or environments "
                    "look highly detailed or photorealistic. Look closely for CGI styling or animated characters. "
                    "Do NOT reject it for being animated or showing realistic animated environments."
                )
                contradiction_examples = "a live-action news broadcast, a real-life sports match, a real-world home video with real people, or a static test pattern"
            else:
                genre_context = (
                    f"The expected show/movie '{clean_title}' is a live-action film or series. "
                    "Therefore, the frames should show normal live-action environments/actors."
                )
                contradiction_examples = "a 2D cartoon/anime (unless it's an animated show), a real-life sports match, a news broadcast, a cooking show, a video game, or a home renovation channel"

            # Load feedback chat history for cognitive learning
            chat_history = []
            try:
                feedback_path = self.workspace_root / "data" / "vlm_feedback.json"
                if feedback_path.exists():
                    with open(feedback_path, "r", encoding="utf-8") as f:
                        feedback_items = json.load(f)
                    for item in feedback_items:
                        fb_title = item.get("title")
                        fb_notes = item.get("notes", "")
                        fb_kf_paths = item.get("keyframes_paths", [])
                        fb_images_b64 = []
                        # Encode up to 3 keyframe images of the feedback sample to keep payload manageable
                        for p in fb_kf_paths[:3]:
                            try:
                                fb_images_b64.append(self._encode_image(p))
                            except Exception:
                                pass
                        if fb_images_b64:
                            chat_history.append({
                                "role": "user",
                                "content": f"Here is an example of a video for expected title '{fb_title}'. You previously incorrectly failed it with the reason: '{fb_notes}'. Note that these frames are actually correct and match.",
                                "images": fb_images_b64
                            })
                            chat_history.append({
                                "role": "assistant",
                                "content": json.dumps({
                                    "content_matches": True,
                                    "confidence": 1.0,
                                    "description": f"Verified match for {fb_title}",
                                    "reason": "Acknowledged. This style and setting is correct."
                                })
                            })
            except Exception as e:
                logger.warning(f"Failed to load VLM feedback for chat history: {e}")

            # Since keyframes are sampled at 1m, 2m, 3m, 4m, 5m followed by percentages:
            # First 5 frames are early checks, last frame is credits, middle ones are sanity checks.
            if len(keyframe_paths) >= 6:
                # Mid keyframes are those between early title checks (first 5) and late credits check (last 1)
                mid_keyframes = keyframe_paths[5:-1]
                if not mid_keyframes:
                    mid_keyframes = [keyframe_paths[len(keyframe_paths)//2]]
                images_b64 = [self._encode_image(p) for p in mid_keyframes]
                prompt = (
                    f"You are verifying if a video file matches its expected title: '{clean_title}'.{meta_str}\n\n"
                    f"Analyze these {len(mid_keyframes)} images from the middle of the video:\n"
                    f"1. Identify the setting, genre, and style of the video.\n"
                    f"2. {genre_context}\n"
                    f"3. Your task is to verify that these images are consistent with a standard movie or show of this general genre. You must be EXTREMELY LENIENT.\n"
                    f"4. PASS BY DEFAULT: You should output 'content_matches: true' by default. Normal scenes (talking, sitting, walking, or dark environments) are normal and match the expected media. Do NOT reject the file based on everyday settings, dark frames, or the lack of specific action points.\n"
                    f"5. FLAGRANT CONTRADICTIONS ONLY: Only output 'content_matches: false' if there is an undeniable, obvious contradiction (e.g. {contradiction_examples}).\n"
                    f"Respond with a JSON object:\n"
                    f"{{\n"
                    f"  \"content_matches\": true/false,\n"
                    f"  \"confidence\": 0.0-1.0,\n"
                    f"  \"description\": \"brief summary of detected elements\",\n"
                    f"  \"reason\": \"explanation of why it matches or contradicts the expected title\"\n"
                    f"}}"
                )
                logger.info("Sending Stage 3 (Sanity Check) prompt with multiple images to Qwen2.5-VL...")
                sanity_resp = await self._query_ollama(prompt, images_b64, chat_history=chat_history)
                logger.info(f"Stage 3 Response: {sanity_resp}")
                results["sanity_check_passed"] = sanity_resp.get("content_matches", False) or sanity_resp.get("sanity_check_passed", False)
                results["raw_logs"].append({"stage": "sanity", "response": sanity_resp})
            elif len(keyframe_paths) >= 3:
                img_b64 = self._encode_image(keyframe_paths[len(keyframe_paths)//2])
                prompt = (
                    f"Analyze this image from a video. Does the scene/visual content match the expectations of a "
                    f"media file titled '{clean_title}'?\n"
                    f"{genre_context}\n"
                    f"You must be EXTREMELY LENIENT. Normal scenes (talking, sitting, walking, or dark environments) are normal. "
                    f"Only set content_matches: false if there is an obvious contradiction (e.g., {contradiction_examples}).\n"
                    f"Answer with a JSON object: "
                    f"{{\n"
                    f"  \"content_matches\": true/false,\n"
                    f"  \"confidence\": 0.0-1.0,\n"
                    f"  \"description\": \"brief summary of detected elements\",\n"
                    f"  \"reason\": \"explanation of why it matches or contradicts the expected title\"\n"
                    f"}}"
                )
                logger.info("Sending Stage 3 (Sanity Check) prompt to Qwen2.5-VL...")
                sanity_resp = await self._query_ollama(prompt, img_b64, chat_history=chat_history)
                logger.info(f"Stage 3 Response: {sanity_resp}")
                results["sanity_check_passed"] = sanity_resp.get("content_matches", False) or sanity_resp.get("sanity_check_passed", False)
                results["raw_logs"].append({"stage": "sanity", "response": sanity_resp})
        except Exception as e:
            logger.error(f"Sanity check VLM failed: {e}")
            results["raw_logs"].append({"stage": "sanity", "error": str(e)})

        return results

    async def _query_ollama(self, prompt: str, images_b64: list | str, chat_history: list = None) -> dict:
        """Sends request to local Ollama API with one or more base64 encoded images, optionally prepended with history."""
        if isinstance(images_b64, str):
            images_b64 = [images_b64]
            
        model = get_system_config("OLLAMA_MODEL", "qwen2.5-vl")
        api_url = get_system_config("OLLAMA_API_URL", "http://localhost:11434")
        
        messages = []
        if chat_history:
            messages.extend(chat_history)
            
        messages.append({
            "role": "user",
            "content": prompt,
            "images": images_b64
        })
        
        payload = {
            "model": model,
            "messages": messages,
            "options": {
                "num_ctx": 16384,
                "temperature": 0.2
            },
            "stream": False
        }
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{api_url}/api/chat", json=payload)
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

    async def verify_extended_dialogue(self, subtitle_text: str, title: str, summary: str) -> dict:
        """Compares subtitle dialogue transcript against expected movie/episode overview using Ollama text completion."""
        # Limit transcript size to avoid context limit (approx 400 lines)
        lines = subtitle_text.split("\n")
        truncated_transcript = "\n".join(lines[:400])
        
        prompt = (
            f"You are a media verification assistant. Verify if the following dialogue transcript is consistent with the expected TV show episode plot summary.\n\n"
            f"Expected Episode Title: {title}\n"
            f"Expected Episode Plot Overview: {summary}\n\n"
            f"Dialogue Transcript:\n{truncated_transcript}\n\n"
            f"Your task is to analyze if the characters, key events, discussions, or topics in the dialogue transcript are consistent with or could reasonably belong to the expected episode. You must be EXTREMELY LENIENT.\n"
            f"Note that dialogue transcripts from subtitles may be conversational, fragmented, or cover only a portion of the episode, and may not explicitly state every plot point. If there are matching characters, topics, references, or general alignment, you must set matched to true.\n"
            f"Only set matched to false if there is a clear, flagrant contradiction indicating this dialogue is from an entirely different show or movie.\n\n"
            f"Respond with a JSON object: {{\"matched\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"string\"}}"
        )
        
        try:
            resp = await self._query_ollama(prompt, [])
            return {
                "matched": resp.get("matched", False) or resp.get("title_found", False),
                "reason": resp.get("reason", "Dialogue evaluation completed.")
            }
        except Exception as e:
            logger.error(f"Ollama extended dialogue audit failed: {e}")
            return {
                "matched": False,
                "reason": f"Extended dialogue check failed: {str(e)}"
            }

    async def verify_visuals_dense(self, keyframe_paths: list, title: str, summary: str, filepath: str = None) -> dict:
        """Processes a dense sequence of keyframes to verify if the visuals match the expected movie/episode plot."""
        results = {"matched": False, "reason": "No keyframes provided."}
        if not keyframe_paths:
            return results
            
        # We can send up to 12 keyframes in a single request (evenly spaced from the dense list) to avoid overloading visual context
        sampled_paths = keyframe_paths
        if len(keyframe_paths) > 12:
            step = len(keyframe_paths) / 12.0
            sampled_paths = [keyframe_paths[int(i * step)] for i in range(12)]
            
        # Determine animated context dynamically
        title_lower = title.lower()
        path_lower = filepath.lower() if filepath else ""
        animated_keywords = [
            "simpsons", "futurama", "family guy", "american dad", "robot chicken", 
            "ren and stimpy", "ren & stimpy", "ariel", "arthur", "bunnicula", 
            "daniel tiger", "duckman", "garfield", "reboot", "rick and morty", 
            "rick & morty", "trash truck", "looney tunes", "mickey mouse", "cartoon",
            "animated", "animation"
        ]
        is_animated = any(kw in title_lower or kw in path_lower for kw in animated_keywords)
        
        if is_animated:
            genre_context = (
                f"The expected show/movie '{title}' is an ANIMATED cartoon or CGI movie. "
                "Look closely for CGI styling or animated characters. "
                "Do NOT reject it for being animated or showing realistic animated environments."
            )
            contradiction_examples = "a live-action news broadcast, a real-life sports match, a real-world home video with real people, or a static test pattern"
        else:
            genre_context = (
                f"The expected show/movie '{title}' is a live-action film or series. "
                "Therefore, the frames should show normal live-action environments/actors."
            )
            contradiction_examples = "a 2D cartoon/anime (unless it's an animated show), a real-life sports match, a news broadcast, a cooking show, a video game, or a home renovation channel"

        try:
            images_b64 = [self._encode_image(p) for p in sampled_paths]
            prompt = (
                f"You are a media verification assistant. Analyze this sequence of {len(sampled_paths)} frames taken throughout a video.\n\n"
                f"Expected Show/Movie Title: {title}\n"
                f"Expected Plot Summary: {summary}\n\n"
                f"Genre Context: {genre_context}\n\n"
                f"Your task is to verify if the visual content of these frames (settings, characters, scenes, storylines) depicts events consistent with the expected title/plot summary. You must be EXTREMELY LENIENT.\n"
                f"Remember that a small set of sampled frames cannot capture every detail, scene, or subplot of the expected plot summary.\n"
                f"If the settings, characters, style, or any major element in these frames matches or is highly consistent with the expected title/summary, you must set matched to true.\n"
                f"Only set matched to false if there is an undeniable, obvious contradiction (e.g. {contradiction_examples}).\n\n"
                f"Respond with a JSON object: {{\"matched\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"string\"}}"
            )
            
            resp = await self._query_ollama(prompt, images_b64)
            return {
                "matched": resp.get("matched", False) or resp.get("content_matches", False),
                "reason": resp.get("reason", "VLM dense check did not return a reason.")
            }
        except Exception as e:
            logger.error(f"Ollama dense visuals audit failed: {e}")
            return {"matched": False, "reason": f"VLM dense verification failed: {str(e)}"}
