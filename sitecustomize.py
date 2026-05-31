from __future__ import annotations

try:
    from sniperplug.services.warning_filters import install_warning_filters

    install_warning_filters()
except Exception:
    pass
