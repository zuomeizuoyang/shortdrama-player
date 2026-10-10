"""A verified adapter for ReelShort's public website."""
from .catalog import CatalogMixin
from .client import ReelShortClient, ReelShortError
from .details import DetailsMixin
from .playback import PlaybackMixin


class ReelShortAdapter(CatalogMixin, DetailsMixin, PlaybackMixin, ReelShortClient):
    """Catalog, search, metadata, episodes and entitled playback in one client."""


__all__ = ["ReelShortAdapter", "ReelShortError"]
