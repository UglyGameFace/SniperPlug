# Movie screening city awareness (#273)

This branch resumes the suspended movie-ticket truthfulness task without changing the repository-wide Walmart `ACTIVE_TASK.md` owned by separate work.

## Root cause

The saved-ZIP local filter already resolves a user's ZIP/radius and verifies SeeItEarly city pages, but source-aware screening locality is incomplete. In particular, a Gofobo title can expose a title-specific national listing while only a subset of cities actually has a Gofobo free-pass claim. Treating the title as merely distance-unverified hides useful city coverage, while treating any same-title screening as local can point a user at the wrong provider.

## Required behavior

- parse the public SeeItEarly movie page's explicit free-pass city blocks;
- take city/state identity only from `/movies/<slug>/in/<city-state>` URLs rather than guessing ambiguous city names;
- retain the actual free-pass provider host for each city;
- a Gofobo result is local only when the nearby city/state has an explicit Gofobo free-pass link;
- SeeItEarly retains its existing city-page compatibility fallback when movie-detail coverage is temporarily unavailable;
- hidden titles with confirmed city coverage explain where they are actually available, e.g. `Resident Evil: Miami, FL only`;
- all screening coverage/network failures remain fail-closed;
- existing national reusable promotions, saved ZIP persistence, radius rules, and server alert configuration remain unchanged.

## Validation gate

Do not merge until exact-head Termux/Ubuntu validation passes targeted location/coverage tests, existing movie location/parser/latency tests, the relevant movie result/runtime tests, compile/import, native libsql, full pytest, diff check, conflict scan, clean worktree, and live public coverage probes for current titles such as Forgotten Island.
