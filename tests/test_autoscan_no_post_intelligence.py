from types import SimpleNamespace

from sniperplug.services.autoscan_no_post_intelligence import build_autoscan_no_post_intelligence


class Result(SimpleNamespace):
    posted: int = 0
    skipped_duplicate: int = 0
    skipped_recent_alert_duplicate: int = 0
    skipped_reserved_duplicate: int = 0
    skipped_not_alertable: int = 0
    skipped_disabled: int = 0
    skipped_wrong_retailer: int = 0
    errors: tuple[str, ...] = ()


def report(**kwargs):
    defaults = {
        "products_checked": 994,
        "searches_checked": 40,
        "verified_before_memory": 0,
        "total_cards": 0,
        "fresh_cards": 0,
        "cards_attempted_for_public": 0,
        "price_memory_summary": "observed price memory: new: **800** • same_or_higher: **120** • public price-drop cards: **0**",
        "verification_failure_summary": "0 verified markdown cards. Main blockers: weak/ignored reference: **550** • missing trusted was/reference: **55**",
        "review_candidate_summary": "review candidates: **25** • strongest private leads kept: Hyper Tough Tire Inflator",
        "repeat_summary": "fresh/new public-ready: **0** • private review/scout leads kept out of public: **3**",
        "route_summary": "• `tool rollback` — **249** products across **10** page(s)",
        "warnings": ("Public Scout Lane is disabled for public posts.",),
        "public_result": Result(),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_no_post_intelligence_reports_full_public_funnel_and_memory():
    text = build_autoscan_no_post_intelligence(report())

    assert "Scan volume" in text
    assert "994" in text
    assert "Verified/public funnel" in text
    assert "verified before memory **0**" in text
    assert "Observed price memory" in text
    assert "public price-drop cards" in text
    assert "Proof blockers" in text
    assert "Private review/scout leads" in text
    assert "Fresh/repeat gate" in text
    assert "Top routes checked" in text
    assert "Bottom line" in text


def test_no_post_intelligence_calls_out_final_public_guard_blocks():
    text = build_autoscan_no_post_intelligence(
        report(
            total_cards=2,
            fresh_cards=2,
            cards_attempted_for_public=2,
            public_result=Result(
                posted=0,
                skipped_duplicate=1,
                skipped_not_alertable=1,
                skipped_recent_alert_duplicate=1,
            ),
        )
    )

    assert "Final public guard blocks" in text
    assert "duplicates **1**" in text
    assert "same/higher recent posts **1**" in text
    assert "public-quality blocked **1**" in text


def test_no_post_intelligence_says_when_memory_is_not_active():
    text = build_autoscan_no_post_intelligence(report(price_memory_summary="not used"))

    assert "Observed price memory: **not active on this run**" in text
    assert "Walmart's own trusted was/reference fields" in text
