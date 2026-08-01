from __future__ import annotations

from sniperplug.services import walmart_product_metadata as metadata
from sniperplug.services.walmart_metadata_snapshot_guard import (
    bounded_snapshot_payload,
    install_bounded_walmart_metadata_snapshot,
)


def test_snapshot_guard_installs_idempotently() -> None:
    install_bounded_walmart_metadata_snapshot()
    install_bounded_walmart_metadata_snapshot()

    assert metadata._snapshot_payload is bounded_snapshot_payload
    assert metadata._sniperplug_bounded_snapshot_installed is True
