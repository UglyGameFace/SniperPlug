"""Standalone eBay listing and verified extreme-drop watcher."""

from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.service import EbayWatcherService

__all__ = ["EbayWatcherService", "EbayWatcherSettings"]
