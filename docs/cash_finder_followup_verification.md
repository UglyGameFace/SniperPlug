# Cash Finder follow-up verification

Run after pulling this follow-up branch:

```bash
cd ~/SniperPlug || exit 1
python -m compileall sniperplug
pytest -q tests/test_cashfinder_timeout_truth_static.py tests/test_cashfinder_followup_static.py tests/test_stale_compat_modules_static.py tests/test_runtime_compat_imports.py tests/test_walmart_cash_badge_pdp_enrichment.py tests/test_walmart_public_lanes.py tests/test_public_deal_surface_regression_static.py
pytest -q
```

If local Termux says `No module named 'aiosqlite'`, install declared dependencies first:

```bash
pip install -r requirements.txt
```
