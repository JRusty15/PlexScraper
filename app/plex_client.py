import os
import logging
from plexapi.server import PlexServer

logger = logging.getLogger("plex_client")

PLEX_URL = os.environ.get("PLEX_URL", "")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")

class PlexClient:
    def __init__(self):
        self.server = None
        if PLEX_URL and PLEX_TOKEN:
            try:
                self.server = PlexServer(PLEX_URL, PLEX_TOKEN)
                logger.info("Connected to Plex Media Server.")
            except Exception as e:
                logger.error(f"Failed to connect to Plex Media Server: {e}")

    def get_all_paths_mapping(self) -> dict:
        """Queries all library items from Plex in a single pass to build a file path mapping."""
        mapping = {}
        if not self.server:
            return mapping
            
        try:
            logger.info("Building path-to-metadata mapping from Plex library...")
            for section in self.server.library.sections():
                for item in section.all():
                    if item.type in ["movie", "episode"]:
                        for media in item.media:
                            for part in media.parts:
                                if part.file:
                                    norm_path = os.path.normpath(part.file)
                                    duration_sec = media.duration / 1000.0 if media.duration else None
                                    mapping[norm_path] = (duration_sec, item.ratingKey)
            logger.info(f"Plex mapping complete. Mapped {len(mapping)} files.")
        except Exception as e:
            logger.error(f"Error mapping Plex files: {e}")
            
        return mapping

    def get_duration_and_rating_key(self, filepath: str) -> tuple:
        """Finds matching item in Plex libraries and returns expected duration (seconds) and ratingKey."""
        if not self.server:
            return None, None
            
        try:
            # Enumerate library sections (Movies and TV Shows)
            for section in self.server.library.sections():
                # Search items in section
                # Since section.search searches by title, it might be easier to query section.all()
                # or match using file path.
                for item in section.all():
                    # For movies, there is usually one media item, for shows there are episodes
                    if item.type in ["movie", "episode"]:
                        for media in item.media:
                            for part in media.parts:
                                if os.path.normpath(part.file) == os.path.normpath(filepath):
                                    # Duration is in milliseconds in PlexAPI, convert to seconds
                                    duration_sec = media.duration / 1000.0 if media.duration else None
                                    return duration_sec, item.ratingKey
        except Exception as e:
            logger.error(f"Plex search error for {filepath}: {e}")
            
        return None, None

    def get_metadata(self, rating_key: str) -> dict:
        """Fetches detailed TMDB metadata from local Plex Server for verification guidance."""
        meta = {
            "summary": "",
            "genres": [],
            "roles": [],
            "year": None
        }
        if not self.server or not rating_key:
            return meta
            
        try:
            item = self.server.fetchItem(int(rating_key))
            meta["summary"] = getattr(item, "summary", "")
            meta["genres"] = [g.tag for g in getattr(item, "genres", [])] if hasattr(item, "genres") else []
            meta["roles"] = [r.tag for r in getattr(item, "roles", [])][:5] if hasattr(item, "roles") else []
            meta["year"] = getattr(item, "year", None)
        except Exception as e:
            logger.error(f"Error fetching metadata for rating key {rating_key}: {e}")
            
        return meta
