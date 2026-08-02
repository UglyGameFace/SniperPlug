"""Standalone HP Store catalog and exact-price watcher."""

from sniperplug.hp_watcher.config import HPWatcherSettings
from sniperplug.hp_watcher.service import HPWatcherService

__all__ = ["HPWatcherService", "HPWatcherSettings"]
