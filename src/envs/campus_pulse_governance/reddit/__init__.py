"""Reddit CMV data and world adapters for ForumTwin.

Raw corpus handling stays local.  This package deliberately reuses the
existing ForumTwin runtime instead of defining a second forum engine.
"""

from .ingest import RedditIngestError

__all__ = ["RedditIngestError"]
