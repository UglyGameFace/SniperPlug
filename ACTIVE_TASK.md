# Active Task

## Status
In progress — replace the original single-location Target watcher configuration with location-safe multi-tenant server and user profiles.

## Scope
Target must use one global catalog and one shared watcher process while preserving exact store/ZIP pricing and fulfillment for every SniperPlug server and personal DM subscriber. No owner, Connecticut, or process-wide location may be used as a fallback.

## Root cause
- PR #199 correctly required exact Target store/ZIP proof but put one store, ZIP, state, latitude, and longitude in the worker environment.
- That model could only represent one location and would have attached the owner's test location to every server.
- Target was also auto-enrolled for existing Walmart destinations before those servers had chosen a Target store.
- Fanout originally trusted the event proof but did not independently compare its store/ZIP to each destination's saved location.

## Changes
- Removed process-wide Target store, ZIP, state, latitude, and longitude settings.
- Added Turso-backed guild and user Target location profiles.
- Added strict nearby-store parsing and Discord ZIP -> store dropdown setup.
- Added `/target_location`, `/target_location_clear`, `/target_dm_location`, and `/target_dm_location_clear` to the canonical command surface.
- Target is added to a server's retailer list only after an admin selects an exact store.
- Added startup remediation that removes the original unsafe Target enrollment from guilds without a saved location.
- Added one global TCIN catalog and bounded per-unique-location cursors.
- Servers and users sharing the same `store_id + ZIP` share one location scan.
- Every RedSky product and fulfillment request requires an explicit saved location.
- Added exact location matching in both retailer fanout and Target's public posting function.
- Added explicit location information to Target event proof and health output.
- Capped product and per-location fulfillment batches at RedSky's 24-TCIN limit.
- Removed duplicate/unregistered Target command definitions.
- Updated deployment documentation so only Turso credentials and the Target key are global secrets.

## Safety behavior
- Existing servers inherit no Target location.
- Target remains disabled until a server admin chooses a store.
- Personal Target DMs remain disabled until that user chooses a store.
- Store/ZIP mismatch blocks delivery even when all other deal proof is valid.
- Missing location, malformed nearby-store data, missing coordinates, or failed RedSky lookup saves nothing.
- A watcher can run with zero saved locations and waits safely for configuration.

## Validation in progress
- Added tests for opt-in Target enrollment.
- Added tests for unsafe-enrollment remediation.
- Added tests for shared unique-location grouping and bounded catalog staging.
- Added tests for exact guild/user fanout matching.
- Added tests for strict nearby-store parsing.
- Added tests proving the watcher has no process-wide location fields and caps fulfillment batches at 24.
- Full repository compilation and pytest workflows still required.
- Final PR review, conflict, changed-file, and temporary-file inspection still required.

## Blockers
None currently.

## Deployment after merge
- Redeploy the main SniperPlug app so startup remediation and location commands are live.
- Deploy the separate Target watcher with only the shared Turso credentials and `TARGET_REDSKY_API_KEY`.
- Do not configure `TARGET_STORE_ID`, `TARGET_ZIP`, `TARGET_STATE`, `TARGET_LATITUDE`, or `TARGET_LONGITUDE`.
