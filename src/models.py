"""Data structures passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class Article:
    """A single candidate article flowing through fetch -> extract -> rank -> refine -> output."""

    title: str
    link: str
    normalized_link: str
    source_name: str
    source_weight: float = 1.0
    source_url: str = ""
    # Category key and label copied from the feed definition; used to group
    # articles into the per-category output feeds.
    category: str = ""
    category_label: str = ""
    published: Optional[datetime] = None
    summary: str = ""
    content: str = ""
    # True when the page body could not be extracted; such articles are dropped
    # instead of being published as an RSS-summary stub.
    extraction_failed: bool = False

    # Ranking results (None when the fallback ordering was used)
    score: Optional[float] = None
    reason: Optional[str] = None

    # Refinement results (None when refinement was skipped or failed)
    refined_content: Optional[str] = None
    refined_title: Optional[str] = None
    refine_failed: bool = False

    @property
    def effective_title(self) -> str:
        return self.refined_title or self.title

    @property
    def effective_content(self) -> str:
        return self.refined_content or self.content

    @property
    def is_refined(self) -> bool:
        return bool(self.refined_content)

    def time_key(self) -> datetime:
        return self.published or MIN_DATETIME

    def sort_key(self) -> tuple[datetime, float]:
        """Fallback ordering key: newest first, then source weight."""
        return (self.time_key(), self.source_weight)
