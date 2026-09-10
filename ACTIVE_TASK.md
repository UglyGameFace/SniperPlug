# Active Task

## Status
**SP-GATEWAY-EVENT-LOOP-LAG — IN PROGRESS.**

Walmart delivery stall work is complete in production. The user explicitly switched the Single Active Task Lock to the repeated Discord gateway/event-loop lag investigation on 2026-09-10.

Base main at task start: `51fdd1782bf9137b7e5d48fa79c010c30205e750` (PR #283 merge, Walmart delivery task closed).

## Production symptom
Production repeatedly emitted discord.py 2.7.1 warnings such as:

- `Can't keep up, shard ID None websocket is 41.3s behind.`
- `Can't keep up, shard ID None websocket is 41.5s behind.`

The warnings were seen near high-throughput Walmart exact drain windows, but timing alone is not proof that Walmart blocked the Discord asyncio loop.

## Root-cause findings before code changes
- SniperPlug already runs a 5-second local event-loop watchdog with a 2-second warning threshold. The supplied production windows contain discord.py `Can't keep up` warnings but no matching SniperPlug `Event loop lag detected` line.
- discord.py 2.7.1's `KeepAliveHandler.ack()` logs `Can't keep up` when heartbeat-to-ACK protocol latency exceeds 10 seconds. A different message, `heartbeat blocked for more than ... seconds`, is used when the event loop cannot execute the heartbeat send coroutine for 10 seconds. The production symptom is the ACK-latency warning, not the heartbeat-send-blocked warning.
- Walmart catalog HTTP/candidate parsing is already moved to worker event loops/threads; exact candidate parsing/preparation is offloaded; native Turso/libSQL is isolated in a child process. Existing #202/#210 hardening must be preserved rather than undone by guessing at another concurrency reduction.
- One definite local blocking class remains: runtime logging installs synchronous console plus rotating-file handlers on the main process. `ProcessIsolatedSniperPlugBot.run()` then calls `discord.py` without `log_handler=None`, so discord.py also installs its default handler. Production confirms the duplicate Discord records. Async workers therefore perform unnecessary synchronous console/file logging work on the Discord loop, and Discord records are emitted twice.

## Implementation scope
1. Move SniperPlug's console/file sink I/O behind a standard-library `QueueHandler`/`QueueListener` so event-loop logging calls only enqueue records.
2. Disable discord.py's duplicate default handler with `bot.run(..., log_handler=None)` and shut the logging listener down cleanly after the bot exits.
3. Correlate Discord heartbeat ACK latency with a rolling local-loop-lag window. Log distinct classifications for:
   - healthy gateway + healthy loop;
   - high gateway ACK latency while the local loop remained healthy;
   - high gateway latency accompanied by real local loop lag.
4. Emit a bounded watchdog health line so production can prove the watchdog itself stayed alive through a gateway-latency event.
5. Do not throttle Walmart, alter proof/retry rules, force Discord reconnects, or weaken delivery behavior without evidence that local workload is the cause.

## Safety invariants
- Walmart #277-#282 retry, pressure-lane, ranking, and delivery behavior stays unchanged.
- Turso/libSQL remains process-isolated and transaction-safe.
- Discord reconnect/RESUME behavior remains owned by discord.py; no forced reconnect loop is introduced.
- Console and rotating-file log content/severity are preserved; only sink execution moves off the event-loop thread and duplicate discord.py handler installation is removed.
- Existing 2-second local loop warning sensitivity remains unchanged.

## Validation required
- Targeted gateway/logging regression tests.
- Existing autoscan production-hardening tests.
- Native libsql process tests.
- Compile/import/pip checks.
- Full pytest suite.
- Diff/scope/conflict-marker/clean-worktree checks on one exact head.
- Production acceptance must show the new logging/watchdog marker, no duplicate Discord log lines, and a truthful gateway-vs-loop classification during any subsequent high-latency event.

## Completed prerequisite — Walmart delivery stall
Production-accepted Walmart main remains `9dcdf197f225e76dcf8adad546d49ea6fe3412e6`; PR #283 later closed the task bookkeeping on main `51fdd1782bf9137b7e5d48fa79c010c30205e750`.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's earlier FORCE SWITCH. Preserve PR #273 and all merged movie-ticket work unchanged.

## Backlog — not active
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Implement and validate the narrow gateway/logging fix on the dedicated branch. Do not switch to another backlog item until this task is production-accepted or the user explicitly FORCE SWITCHes again.
