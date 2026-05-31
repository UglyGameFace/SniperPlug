from __future__ import annotations

import warnings


def install_warning_filters() -> None:
    """Hide known third-party library noise that SniperPlug cannot fix locally.

    discord.py currently calls asyncio.iscoroutinefunction inside command
    decorator internals. Python 3.14 warns that this stdlib helper is deprecated
    for future Python 3.16 removal. This is upstream library noise, not a
    SniperPlug bug, and the right long-term fix is updating discord.py when the
    library ships a release using inspect.iscoroutinefunction.
    """
    warnings.filterwarnings(
        action="ignore",
        message=r".*asyncio\.iscoroutinefunction.*deprecated.*",
        category=DeprecationWarning,
    )
