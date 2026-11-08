from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_open_box_routes_and_structured_card_builder_remain_available():
    # NOTE (C6): the retired /open_box_deals cog (sniperplug/cogs/open_box_deals.py)
    # was deleted — it was never loaded and its build_open_box_cards builder
    # died with it. Open-box coverage lives natively in
    # services/open_box_autoscan_routes.py, which is what this test now pins.
    routes = read("sniperplug/services/open_box_autoscan_routes.py")
    assert "OPEN_BOX_AUTOSCAN_QUERIES" in routes
    for query in (
        "open box vacuum",
        "open box electronics",
        "restored vacuum",
        "refurbished vacuum",
        "like new vacuum",
        "open box appliance",
        "restored electronics",
        "open box home",
        "open box gaming",
        "open box monitor",
        "open box laptop",
    ):
        assert query in routes


def test_open_box_coverage_is_native_without_a_duplicate_loaded_cog():
    bot = read("sniperplug/bot.py")
    routes = read("sniperplug/services/open_box_autoscan_routes.py")
    surface = read("sniperplug/services/command_surface.py")

    assert "from sniperplug.cogs.open_box_deals import OpenBoxDealsCog" not in bot
    assert "await self.add_cog(OpenBoxDealsCog(self))" not in bot
    assert '"open_box_deals"' in surface
    assert "open_box_autoscan_preset" in routes
    assert "OPEN_BOX_AUTOSCAN_KEY" in routes


def test_best_buy_stub_provider_stays_deregistered():
    # NOTE (C6): BestBuyProvider.scan() returns zero candidates by design
    # (staged skeleton, sniperplug/providers/bestbuy.py) — the provider file
    # stays, but it must not be registered in bot.py.
    bot = read("sniperplug/bot.py")

    assert "from sniperplug.providers.bestbuy import BestBuyProvider" not in bot
    assert "BestBuyProvider(" not in bot


def test_canonical_deal_and_autoscan_commands_still_exist():
    scanner = read("sniperplug/cogs/deal_scanner.py")
    verified = read("sniperplug/cogs/verified_deal_scanner.py")
    autoscan = read("sniperplug/cogs/auto_scan_runner.py")
    catalog = read("sniperplug/services/command_catalog.py")

    assert '@app_commands.command(name="deals"' in scanner
    assert '@app_commands.command(name="hunt"' in verified
    assert '@app_commands.command(name="walmart_cash"' in scanner
    assert '@app_commands.command(name="autoscan_now"' in autoscan
    assert 'name="/walmart_scan"' not in catalog


def test_home_depot_penny_and_verizon_surfaces_still_exist():
    home_depot_local = read("sniperplug/cogs/home_depot_local.py")
    home_depot_search = read("sniperplug/cogs/home_depot_search.py")
    verizon = read("sniperplug/cogs/verizon_shine.py")
    assert 'name="home_depot_penny_hunt"' in home_depot_search
    assert 'name="hd_stock"' in home_depot_local
    assert "verizon" in verizon.lower()
