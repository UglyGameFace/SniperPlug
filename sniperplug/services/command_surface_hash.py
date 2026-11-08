"""Hash-change detection for the slash-command surface.

The bot must not sync slash commands on every boot (a needless Discord API
call), but it also must not silently keep serving Discord's last registered
command set after a deploy adds a command such as ``/check_deal``. This
module fingerprints the loaded command surface and persists that fingerprint
durably, so boot can sync exactly once when the surface changes and skip the
sync otherwise.

Fingerprint rules (deliberate):
- A command being added, removed, or renamed changes the hash.
- A parameter being added, removed, renamed, retyped, or having its
  description / choices / min / max / required flag changed changes the hash.
- Cosmetic code changes (docstrings, handler bodies, cog class names, cog
  load order) do NOT change the hash: only the surface Discord registers is
  fingerprinted, and entries are sorted before hashing.
- ``default_permissions`` / ``nsfw`` / ``extras`` are intentionally NOT
  fingerprinted: they are not part of the command-schema payload Discord
  syncs for chat-input commands in this bot, and including them would cause
  spurious syncs. If a future command relies on them, add them here and note
  the hash will change once.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


HASH_FILENAME = "command_surface_hash.txt"


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(value)


def _parameter_entry(parameter: Any) -> dict[str, Any]:
    choices = []
    for choice in tuple(getattr(parameter, "choices", ()) or ()):
        choices.append(
            {
                "name": str(getattr(choice, "name", "") or ""),
                "value": str(getattr(choice, "value", "") or ""),
            }
        )
    choices.sort(key=lambda item: (item["name"], item["value"]))

    min_value = getattr(parameter, "min_value", None)
    max_value = getattr(parameter, "max_value", None)
    return {
        "name": str(getattr(parameter, "name", "") or ""),
        "description": str(getattr(parameter, "description", "") or ""),
        "type": _enum_name(getattr(parameter, "type", None)),
        "required": bool(getattr(parameter, "required", False)),
        "choices": choices,
        "min_value": None if min_value is None else str(min_value),
        "max_value": None if max_value is None else str(max_value),
        "autocomplete": bool(getattr(parameter, "autocomplete", False)),
    }


def _command_entries(
    commands: Iterable[Any], *, parent_name: str = ""
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(command: Any, parent: str) -> None:
        object_id = id(command)
        if object_id in seen:
            return
        seen.add(object_id)

        name = str(getattr(command, "name", "") or "")
        qualified_name = str(
            getattr(command, "qualified_name", "")
            or " ".join(part for part in (parent, name) if part)
            or "<unnamed>"
        )
        parameters = [
            _parameter_entry(parameter)
            for parameter in tuple(getattr(command, "parameters", ()) or ())
        ]
        parameters.sort(key=lambda item: item["name"])
        children = _command_entries(
            tuple(getattr(command, "commands", ()) or ()),
            parent_name=qualified_name,
        )
        entries.append(
            {
                "qualified_name": qualified_name,
                "type": _enum_name(getattr(command, "type", None)),
                "description": str(getattr(command, "description", "") or ""),
                "parameters": parameters,
                "commands": children,
            }
        )

    for command in tuple(commands or ()):
        visit(command, parent_name)
    entries.sort(key=lambda item: item["qualified_name"])
    return entries


def command_surface_fingerprint(commands: Iterable[Any]) -> str:
    """Stable SHA-256 hex fingerprint of the loaded slash-command surface."""

    canonical = json.dumps(
        _command_entries(commands),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def default_hash_store_path(database_path: str | os.PathLike[str]) -> Path:
    """Hash file location: next to the bot database, so it survives restarts.

    The data dir is the durable home for bot state (the SQLite file already
    lives there); /tmp would lose the hash on every reboot and defeat the
    purpose.
    """

    return Path(database_path).expanduser().parent / HASH_FILENAME


class CommandSurfaceHashStore:
    """Durable, atomic persistence for the command-surface fingerprint."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()

    def read(self) -> str | None:
        """Return the stored fingerprint, or None when no hash is stored yet."""

        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return text or None

    def write(self, fingerprint: str) -> None:
        """Persist a fingerprint atomically (temp file + rename)."""

        fingerprint = str(fingerprint or "").strip()
        if not fingerprint:
            raise ValueError("Cannot persist an empty command-surface fingerprint")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(fingerprint + "\n", encoding="utf-8")
        os.replace(tmp_path, self.path)
