# Retired one-shot cleanup scripts (history only)

These five scripts were Termux one-shot cleanups from the duplicate-command /
embed-delivery / Turso-stream-lock work. They were moved out of the repo root
in C6 (2026-11-07) so they are no longer runnable from the root by accident.

- `cleanup_duplicate_commands.sh`
- `finish_duplicate_command_cleanup.sh`
- `finish_embed_patch_cleanup.sh`
- `remove_embed_delivery_startup_patch.sh`
- `fix_turso_stream_lock.sh`

Do not run them: they target Termux paths (`/data/data/com.termux/...`) and
the cleanups they performed are long finished. They are kept here only so
the archaeology survives.
