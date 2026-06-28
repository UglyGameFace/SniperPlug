from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_open_box_command_and_routes_exist():
    source = read("sniperplug/cogs/open_box_deals.py")
    routes = read("sniperplug/services/open_box_autoscan_routes.py")
    assert '@app_commands.command(name="open_box_deals"' in source
    assert "OPEN_BOX_AUTOSCAN_QUERIES" in source
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


def test_open_box_cog_and_autoscan_routes_are_native():
    bot = read("sniperplug/bot.py")
    routes = read("sniperplug/services/open_box_autoscan_routes.py")
    policy = read("sniperplug/services/autoscan_route_policy.py")
    assert "from sniperplug.cogs.open_box_deals import OpenBoxDealsCog" in bot
    assert "await self.add_cog(OpenBoxDealsCog(self))" in bot
    assert "install_open_box_autoscan_routes" not in bot
    assert "install_open_box_autoscan_routes" not in routes
    assert "open_box_autoscan_preset" in routes
    assert "public_autoscan_hunt_presets" in policy
    assert "OPEN_BOX_AUTOSCAN_KEY" in policy


def test_existing_deal_and_autoscan_commands_still_exist():
    scanner = read("sniperplug/cogs/deal_scanner.py")
    autoscan = read("sniperplug/cogs/auto_scan_runner.py")
    assert '@app_commands.command(name="deals"' in scanner
    assert '@app_commands.command(name="hunt"' in scanner
    assert '@app_commands.command(name="walmart_cash"' in scanner
    assert '@app_commands.command(name="walmart_scan"' in scanner
    assert '@app_commands.command(name="autoscan_now"' in autoscan


def test_home_depot_penny_and_verizon_surfaces_still_exist():
    home_depot_local = read("sniperplug/cogs/home_depot_local.py")
    home_depot_search = read("sniperplug/cogs/home_depot_search.py")
    verizon = read("sniperplug/cogs/verizon_shine.py")
    assert "penny" in home_depot_local.lower() or "penny" in home_depot_search.lower()
    assert "home_depot" in home_depot_local.lower() or "home depot" in home_depot_local.lower()
    assert "verizon" in verizon.lower()
    assert "shine" in verizon.lower()


def test_dealcard_native_public_proof_surface_exists():
    scanner = read("sniperplug/cogs/deal_scanner.py")
    for field in (
        "deal_lane:",
        "api_current_price:",
        "api_reference_price:",
        "api_discount_percent:",
        "api_condition:",
        "api_condition_path:",
        "api_reference_path:",
        "api_price_path:",
        "seller_name:",
        "fulfillment_type:",
        "direct_product_url:",
        "variant_attributes:",
    ):
        assert field in scanner
    assert "deal_lane=_first_present" in scanner
    assert "api_current_price=current_value" in scanner
    assert "api_reference_price=reference_value" in scanner


def test_open_box_builder_does_not_set_public_proof_fields_after_card_build():
    source = read("sniperplug/cogs/open_box_deals.py")
    assert "card.deal_lane =" not in source
    assert "card.api_current_price =" not in source
    assert "card.api_reference_price =" not in source
    assert "card.api_discount_percent =" not in source
    assert "card.direct_product_url =" not in source
