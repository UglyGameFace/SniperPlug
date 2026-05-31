from __future__ import annotations


def test_core_modules_import() -> None:
    import sniperplug.bot  # noqa: F401
    import sniperplug.cogs.deal_scanner  # noqa: F401
    import sniperplug.cogs.settings_dashboard  # noqa: F401
    import sniperplug.services.warning_filters  # noqa: F401


def test_warning_filter_installs() -> None:
    from sniperplug.services.warning_filters import install_warning_filters

    install_warning_filters()
