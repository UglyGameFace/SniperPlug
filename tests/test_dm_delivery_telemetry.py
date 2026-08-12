from __future__ import annotations

from types import SimpleNamespace

from sniperplug.services.dm_delivery_telemetry import (
    clear_user_dm_delivery_telemetry,
    dm_rejection_bucket,
    get_user_dm_delivery_telemetry,
    record_user_dm_decision,
    record_user_dm_outcome,
)


def test_dm_rejection_buckets_are_stable_for_matcher_reasons() -> None:
    assert dm_rejection_bucket("exact current/was price proof is incomplete") == "proof"
    assert dm_rejection_bucket("price is above your maximum") == "price"
    assert dm_rejection_bucket("Walmart Cash proof is required") == "cash"
    assert dm_rejection_bucket("none of your required keywords matched") == "keywords"
    assert dm_rejection_bucket("category is muted in your personal DMs") == "category"
    assert dm_rejection_bucket("18% is below the required 35%") == "discount"
    assert dm_rejection_bucket("score 60 is below the required 78") == "score"
    assert dm_rejection_bucket("saves $3.00, below the required $8.00") == "savings"


def test_user_telemetry_explains_filter_duplicate_cap_diversity_and_send_outcomes() -> None:
    user_id = 99112233
    clear_user_dm_delivery_telemetry(user_id)

    record_user_dm_decision(
        user_id,
        event_key="walmart:low-discount",
        decision=SimpleNamespace(
            matched=False,
            reason="18% is below the required 35%",
        ),
    )
    record_user_dm_decision(
        user_id,
        event_key="walmart:matched",
        decision=SimpleNamespace(matched=True, reason="matched"),
    )
    record_user_dm_outcome(user_id, "duplicate")
    record_user_dm_outcome(user_id, "daily_cap")
    record_user_dm_outcome(user_id, "diversity_cap")
    record_user_dm_outcome(user_id, "sent")

    telemetry = get_user_dm_delivery_telemetry(user_id)

    assert telemetry.checked == 2
    assert telemetry.matched == 1
    assert telemetry.rejected_filter == 1
    assert telemetry.rejected_discount == 1
    assert telemetry.skipped_duplicate == 1
    assert telemetry.skipped_daily_cap == 1
    assert telemetry.skipped_diversity == 1
    assert telemetry.sent == 1
    assert telemetry.last_outcome == "sent"
    assert any("discount: 1" in line for line in telemetry.summary_lines())
    assert any("diversity reserve: **1**" in line for line in telemetry.summary_lines())
