import asyncio
import logging
import json
from datetime import datetime
from sqlalchemy.orm import Session
from app.database import SessionLocal, MediaFile, AuditJob, AuditResult, JobStatus, FileStatus
from app.media_processor import MediaProcessor
from app.ai_verifier import AIVerifier
from app.plex_client import PlexClient

logger = logging.getLogger("queue_manager")

class QueueWorker:
    def __init__(self, workspace_root: str = "."):
        self.workspace_root = workspace_root
        self.processor = MediaProcessor(output_dir=f"{workspace_root}/processed_media")
        self.verifier = AIVerifier(workspace_root=workspace_root)
        self.is_running = False
        self.is_paused = False
        self._worker_task = None

    def start(self):
        """Starts the background queue processor worker."""
        if not self.is_running:
            self.is_running = True
            self.is_paused = False
            self._worker_task = asyncio.create_task(self._process_queue_loop())
            logger.info("QueueWorker background task started.")

    def stop(self):
        """Stops the queue processor loop completely."""
        if self.is_running:
            self.is_running = False
            if self._worker_task:
                self._worker_task.cancel()
            logger.info("QueueWorker background task stopped.")

    def pause(self):
        """Pauses processing new jobs, keeping current active job running."""
        self.is_paused = True
        logger.info("QueueWorker paused.")

    def resume(self):
        """Resumes queue processing."""
        self.is_paused = False
        logger.info("QueueWorker resumed.")

    async def _process_queue_loop(self):
        """Pulls and runs jobs sequentially from SQLite database."""
        while self.is_running:
            if self.is_paused:
                await asyncio.sleep(1.0)
                continue

            db: Session = SessionLocal()
            try:
                # Find oldest PENDING audit job
                job = db.query(AuditJob).filter(AuditJob.status == JobStatus.PENDING).order_by(AuditJob.created_at.asc()).first()
                
                if not job:
                    await asyncio.sleep(2.0)
                    continue

                # Mark job as processing
                job.status = JobStatus.PROCESSING
                media_file = job.media_file
                media_file.status = FileStatus.VERIFYING
                db.commit()

                logger.info(f"Processing Job #{job.id} for File: {media_file.filename}")
                
                # Perform the Audit
                await self._run_audit_pipeline(db, media_file, job)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue loop: {e}")
                await asyncio.sleep(2.0)
            finally:
                db.close()

    async def _run_audit_pipeline(self, db: Session, media_file: MediaFile, job: AuditJob):
        """Pipeline sequence: metadata probe, keyframe generation, audio extraction, AI validation."""
        result = AuditResult(
            media_file_id=media_file.id,
            status=FileStatus.FAILED
        )
        
        try:
            # Stage 1: Parse and validate container structural info
            meta = self.processor.parse_metadata(media_file.filepath)
            result.ffprobe_valid = True
            result.duration_actual = meta["duration"]
            result.container_format = meta["container"]
            result.video_codec = meta["video_codec"]
            result.audio_codec = meta["audio_codec"]
            result.audio_tracks_info = json.dumps(meta["audio_tracks"])
            
            # Check duration variance if we have expected duration from Plex/metadata
            if media_file.expected_duration:
                variance = abs(meta["duration"] - media_file.expected_duration) / media_file.expected_duration
                result.duration_variance = variance
                if variance > 0.05:
                    result.status = FileStatus.FLAGGED_DURATION
                    result.notes = f"Duration variance exceeds 5% (Expected: {media_file.expected_duration}s, Actual: {meta['duration']}s)"
            
            # Stage 2: Sampling and Extraction
            logger.info("Extracting keyframes...")
            kf_paths = self.processor.extract_keyframes(media_file.filepath, meta["duration"], media_file.id)
            result.keyframes_paths = json.dumps(kf_paths)
            
            logger.info("Extracting audio clips...")
            audio_paths = self.processor.extract_audio_clips(media_file.filepath, meta["duration"], media_file.id)
            result.audio_clips_paths = json.dumps(audio_paths)

            # Check if previous stages already flagged duration. If not, do VLM / Whisper checks.
            if result.status != FileStatus.FLAGGED_DURATION:
                logger.info("Running AI visual checks...")
                
                # Fetch metadata from Plex if rating key exists
                plex_meta = None
                if media_file.plex_rating_key:
                    try:
                        plex = PlexClient()
                        plex_meta = plex.get_metadata(media_file.plex_rating_key)
                        logger.info(f"Loaded Plex/TMDB metadata: {list(plex_meta.keys())}")
                    except Exception as e:
                        logger.error(f"Error fetching Plex metadata for queue verifier: {e}")

                vlm_res = await self.verifier.verify_visuals(
                    kf_paths, 
                    media_file.title or media_file.filename,
                    metadata=plex_meta
                )
                result.vlm_title_verified = vlm_res["title_verified"]
                result.vlm_credits_verified = vlm_res["credits_verified"]
                result.vlm_sanity_check_passed = vlm_res["sanity_check_passed"]
                result.vlm_raw_response = json.dumps(vlm_res["raw_logs"])
                
                logger.info("Running Speech LID language checks...")
                audio_res = await self.verifier.transcribe_audio_and_identify_language(audio_paths)
                result.detected_languages = ",".join(audio_res["languages"])
                result.audio_transcript_snippet = audio_res["transcript"]
                
                # Flag Logic
                vlm_summary = ""
                for log in vlm_res.get("raw_logs", []):
                    stage_name = log.get("stage", "").upper()
                    resp = log.get("response", {})
                    reason_msg = resp.get("reason") or resp.get("raw_text_fallback") or json.dumps(resp)
                    vlm_summary += f"[{stage_name}]: {reason_msg}. "
                
                # Visual verification passes if title is found OR content consistency sanity check passes
                visual_check_passed = vlm_res["title_verified"] or vlm_res["sanity_check_passed"]
                
                # Language override: if ffprobe detected an explicit 'eng' audio track in the headers,
                # we bypass Whisper verification failures as it acts as high confidence metadata proof.
                has_eng_track = False
                if meta.get("audio_tracks"):
                    for track in meta["audio_tracks"]:
                        if track.get("language") == "eng":
                            has_eng_track = True
                            break
                            
                language_check_passed = ("en" in audio_res["languages"]) or has_eng_track
                
                if not visual_check_passed:
                    result.status = FileStatus.FLAGGED_TITLE
                    result.notes = f"Visual verification failed. Title card not verified and sanity check failed. VLM details: {vlm_summary}"
                elif not language_check_passed:
                    result.status = FileStatus.FLAGGED_LANGUAGE
                    result.notes = f"English audio check failed. Detected: {result.detected_languages}. VLM details: {vlm_summary}"
                else:
                    result.status = FileStatus.VERIFIED
                    notes_prefix = "All verification steps passed. " if vlm_res["title_verified"] else "Title card not verified, but visual sanity check passed. "
                    result.notes = f"{notes_prefix}VLM details: {vlm_summary}"
                    
                # If flagged, append details to a dedicated failures log file
                if result.status != FileStatus.VERIFIED:
                    try:
                        log_dir = Path(self.workspace_root) / "data"
                        log_dir.mkdir(parents=True, exist_ok=True)
                        log_file = log_dir / "failures.log"
                        with open(log_file, "a", encoding="utf-8") as f:
                            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                            log_entry = (
                                f"=== FAILURE LOG: {timestamp} ===\n"
                                f"File: {media_file.filename}\n"
                                f"Path: {media_file.filepath}\n"
                                f"Status: {result.status}\n"
                                f"Details: {result.notes}\n"
                                f"==================================\n\n"
                            )
                            f.write(log_entry)
                    except Exception as le:
                        logger.error(f"Failed to write to failures.log: {le}")

                # Calculate final confidence score
                result.confidence_score = self._calculate_confidence(result, vlm_res, audio_res, media_file)
                    
            # Complete Job
            job.status = JobStatus.COMPLETED
            media_file.status = result.status
            
        except Exception as e:
            logger.error(f"Audit pipeline failed for {media_file.filename}: {e}")
            result.status = FileStatus.FLAGGED_CORRUPT
            result.notes = f"Extraction or parsing failure: {str(e)}"
            result.confidence_score = 0
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            media_file.status = FileStatus.FLAGGED_CORRUPT
            
            # Log parsing failures as well
            try:
                log_file = Path(self.workspace_root) / "data" / "failures.log"
                with open(log_file, "a", encoding="utf-8") as f:
                    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"=== EXTRACTION/PARSING FAILURE: {timestamp} ===\nFile: {media_file.filename}\nError: {str(e)}\n==================================\n\n")
            except Exception:
                pass
            
        db.add(result)
        db.commit()

    def _calculate_confidence(self, result: AuditResult, vlm_res: dict, audio_res: dict, media_file: MediaFile) -> int:
        """Calculates confidence score (0-100%) based on validation metrics."""
        score = 0
        
        # 1. Integrity check (ffprobe) - 20 points
        if result.ffprobe_valid:
            score += 20
        else:
            return 0
            
        # 2. Duration check - 25 points
        expected = media_file.expected_duration if media_file else None
        if not expected:
            score += 25
        elif result.duration_variance is not None:
            var = result.duration_variance
            if var <= 0.01:
                score += 25
            elif var > 0.05:
                score += 0
            else:
                score += int(25 * (1.0 - (var - 0.01) / 0.04))
                
        # 3. Visual Checks (Title or Sanity Check) - 35 points
        title_found = vlm_res.get("title_verified", False)
        sanity_passed = vlm_res.get("sanity_check_passed", False)
        
        # Get raw response objects for confidences
        title_conf = 1.0
        sanity_conf = 1.0
        credits_conf = 1.0
        
        for log in vlm_res.get("raw_logs", []):
            stage = log.get("stage", "")
            resp = log.get("response", {})
            conf = resp.get("confidence", 1.0)
            try:
                conf_val = float(conf)
            except Exception:
                conf_val = 1.0
                
            if stage == "title":
                title_conf = conf_val
            elif stage == "sanity":
                sanity_conf = conf_val
            elif stage == "credits":
                credits_conf = conf_val
                
        if title_found:
            score += int(35 * title_conf)
        elif sanity_passed:
            score += int(35 * sanity_conf)
            
        # 4. Credits Check - 10 points
        credits_found = vlm_res.get("credits_verified", False)
        if credits_found:
            score += int(10 * credits_conf)
            
        # 5. Language Check - 10 points
        languages = audio_res.get("languages", [])
        if "en" in languages:
            score += 10
            
        return max(0, min(100, score))

# Single global worker instance reference
worker = QueueWorker()
