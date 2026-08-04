import os
import logging
from plexapi.server import PlexServer
from app.database import get_system_config

logger = logging.getLogger("plex_client")

class PlexClient:
    def __init__(self):
        self.server = None
        plex_url = get_system_config("PLEX_URL", "")
        plex_token = get_system_config("PLEX_TOKEN", "")
        if plex_url and plex_token:
            try:
                self.server = PlexServer(plex_url, plex_token)
                logger.info("Connected to Plex Media Server.")
            except Exception as e:
                logger.error(f"Failed to connect to Plex Media Server: {e}")

    def _get_path_suffix(self, filepath: str) -> str:
        """Extracts the last two components of the file path (e.g. Folder/file.mkv) for robust container matching."""
        if not filepath:
            return ""
        norm = os.path.normpath(filepath).replace("\\", "/")
        parts = norm.split("/")
        if len(parts) >= 2:
            return "/".join(parts[-2:]).lower()
        return norm.lower()

    def get_all_paths_mapping(self) -> dict:
        """Queries all library items from Plex in a single pass to build a file path mapping."""
        mapping = {}
        if not self.server:
            return mapping
            
        try:
            logger.info("Building path-to-metadata mapping from Plex library...")
            for section in self.server.library.sections():
                if section.type == "movie":
                    items = section.all()
                elif section.type == "show":
                    items = section.search(libtype="episode")
                else:
                    continue
                    
                for item in items:
                    for media in item.media:
                        for part in media.parts:
                            if part.file:
                                    norm_path = os.path.normpath(part.file)
                                    duration_sec = media.duration / 1000.0 if media.duration else None
                                    mapping[norm_path] = (duration_sec, item.ratingKey)
                                    
                                    # Fallback mapping using last 2 components
                                    suffix = self._get_path_suffix(part.file)
                                    if suffix:
                                        mapping[suffix] = (duration_sec, item.ratingKey)
                                        
                                    # Fallback mapping using filename (basename) lowercase
                                    filename = os.path.basename(part.file).lower()
                                    if filename:
                                        mapping[filename] = (duration_sec, item.ratingKey)
            logger.info(f"Plex mapping complete. Mapped {len(mapping)} keys/suffixes.")
        except Exception as e:
            logger.error(f"Error mapping Plex files: {e}")
            
        return mapping

    def get_duration_and_rating_key(self, filepath: str) -> tuple:
        """Finds matching item in Plex libraries and returns expected duration (seconds) and ratingKey."""
        if not self.server:
            return None, None
            
        try:
            target_suffix = self._get_path_suffix(filepath)
            for section in self.server.library.sections():
                for item in section.all():
                    if item.type in ["movie", "episode"]:
                        for media in item.media:
                            for part in media.parts:
                                if part.file:
                                    if os.path.normpath(part.file) == os.path.normpath(filepath) or self._get_path_suffix(part.file) == target_suffix:
                                        duration_sec = media.duration / 1000.0 if media.duration else None
                                        return duration_sec, item.ratingKey
        except Exception as e:
            logger.error(f"Plex search error for {filepath}: {e}")
            
        return None, None

    def get_metadata(self, rating_key: str) -> dict:
        """Fetches detailed TMDB metadata from local Plex Server for verification guidance."""
        meta = {
            "title": "",
            "summary": "",
            "genres": [],
            "roles": [],
            "year": None
        }
        if not self.server or not rating_key:
            return meta
            
        try:
            item = self.server.fetchItem(int(rating_key))
            meta["title"] = getattr(item, "title", "")
            meta["summary"] = getattr(item, "summary", "")
            meta["genres"] = [g.tag for g in getattr(item, "genres", [])] if hasattr(item, "genres") else []
            meta["roles"] = [r.tag for r in getattr(item, "roles", [])][:5] if hasattr(item, "roles") else []
            meta["year"] = getattr(item, "year", None)
        except Exception as e:
            logger.error(f"Error fetching metadata for rating key {rating_key}: {e}")
            
        return meta
