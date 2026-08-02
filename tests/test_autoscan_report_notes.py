from sniperplug.cogs.native_auto_scan_runner import select_user_facing_report_notes


def test_report_notes_prioritize_coverage_cash_and_exact_queue() -> None:
    notes = select_user_facing_report_notes(
        (
            "WALMART_PUBLISHER_ID is blank; using direct Walmart links for personal deal hunting.",
            "Autoscan lightweight scan: skipped per-route writes.",
            "Walmart exact-detail queue: discovered 291; due/pending 334",
            "Global exact-detail queue results: foreground saved 2; true overflow verified added 12.",
            "Official Walmart detail gate: 289 candidates retained in the queue.",
            "Catalog-wide route rotation: slot 4/33; this pass 8 routes; full route pool 263.",
            "Walmart Cash routes are included for discovery and strict amounts attach to cards.",
            "Verified-only result policy: uncertain candidates are suppressed.",
            "Suppressed 14 unverified candidates.",
            "Refreshed 13 exact Walmart cards.",
        ),
        limit=4,
    )

    assert notes[0].startswith("Catalog-wide route rotation:")
    assert notes[1].startswith("Walmart Cash routes are included")
    assert notes[2].startswith("Walmart exact-detail queue:")
    assert notes[3].startswith("Global exact-detail queue results:")
    assert all("WALMART_PUBLISHER_ID" not in note for note in notes)
    assert all("Autoscan lightweight" not in note for note in notes)


def test_report_notes_hide_internal_policy_chatter() -> None:
    notes = select_user_facing_report_notes(
        (
            "WALMART_PUBLISHER_ID is blank",
            "Autoscan lightweight scan: skipped writes",
            "Verified-only result policy: exact proof required",
            "Suppressed 10 unverified candidates",
            "Refreshed 12 exact Walmart cards",
        )
    )

    assert notes == ()
