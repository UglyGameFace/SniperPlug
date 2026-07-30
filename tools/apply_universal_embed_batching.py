from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"{label} not found")
    return text.replace(old, new, 1)


def patch_deal_scanner() -> None:
    path = Path("sniperplug/cogs/deal_scanner.py")
    text = path.read_text()
    text = replace_once(
        text,
        "from sniperplug.services.candidate_pipeline import evaluate_candidate\n",
        "from sniperplug.services.candidate_pipeline import evaluate_candidate\n"
        "from sniperplug.services.embed_delivery import send_summary_and_card_batches\n",
        "deal scanner batching import",
    )
    replacements = (
        (
            "        await safe_send_interaction(interaction, embeds=[summary] + [card.embed for card in shown_cards], ephemeral=True)\n",
            "        await send_summary_and_card_batches(interaction, summary=summary, cards=list(shown_cards), ephemeral=True)\n",
            "Walmart Cash delivery",
        ),
        (
            "            await interaction.followup.send(embeds=[summary] + [card.embed for card in shown_cards], view=DealSearchControlView(query, page, max(0, shown_discount), max_results, sort_value, order_value, alerts_only, simple_mode, shown_cards, result.has_next_page), ephemeral=True)\n",
            "            await send_summary_and_card_batches(\n"
            "                interaction,\n"
            "                summary=summary,\n"
            "                cards=list(shown_cards),\n"
            "                view_factory=lambda _batch: DealSearchControlView(query, page, max(0, shown_discount), max_results, sort_value, order_value, alerts_only, simple_mode, shown_cards, result.has_next_page),\n"
            "                ephemeral=True,\n"
            "            )\n",
            "base Walmart scan delivery",
        ),
        (
            "            await interaction.followup.send(embeds=[summary] + [card.embed for card in shown_cards], view=PresetResultView(shown_cards), ephemeral=True)\n",
            "            await send_summary_and_card_batches(\n"
            "                interaction,\n"
            "                summary=summary,\n"
            "                cards=list(shown_cards),\n"
            "                view_factory=lambda _batch: PresetResultView(shown_cards),\n"
            "                ephemeral=True,\n"
            "            )\n",
            "preset hunt delivery",
        ),
        (
            "                await interaction.followup.send(embeds=[summary] + [card.embed for card in shown_cards], view=self._copy_for(page, shown_discount, shown_cards, result.has_next_page), ephemeral=True)\n",
            "                await send_summary_and_card_batches(\n"
            "                    interaction,\n"
            "                    summary=summary,\n"
            "                    cards=list(shown_cards),\n"
            "                    view_factory=lambda _batch: self._copy_for(page, shown_discount, shown_cards, result.has_next_page),\n"
            "                    ephemeral=True,\n"
            "                )\n",
            "rerun delivery",
        ),
        (
            "                    await interaction.followup.send(embeds=[summary] + [card.embed for card in shown_cards], view=self._copy_for(page, min_discount, shown_cards, has_next_page), ephemeral=True)\n",
            "                    await send_summary_and_card_batches(\n"
            "                        interaction,\n"
            "                        summary=summary,\n"
            "                        cards=list(shown_cards),\n"
            "                        view_factory=lambda _batch: self._copy_for(page, min_discount, shown_cards, has_next_page),\n"
            "                        ephemeral=True,\n"
            "                    )\n",
            "80 percent hunt delivery",
        ),
        (
            "                await interaction.followup.send(embeds=[summary] + [card.embed for card in shown_cards], view=self._copy_for(self.page, shown_discount, shown_cards, has_next_page), ephemeral=True)\n",
            "                await send_summary_and_card_batches(\n"
            "                    interaction,\n"
            "                    summary=summary,\n"
            "                    cards=list(shown_cards),\n"
            "                    view_factory=lambda _batch: self._copy_for(self.page, shown_discount, shown_cards, has_next_page),\n"
            "                    ephemeral=True,\n"
            "                )\n",
            "80 percent fallback delivery",
        ),
    )
    for old, new, label in replacements:
        text = replace_once(text, old, new, label)
    text = text.replace("cards=shown_cards,", "cards=list(shown_cards),")
    path.write_text(text)


def patch_home_depot() -> None:
    path = Path("sniperplug/cogs/home_depot_search.py")
    text = path.read_text()
    text = replace_once(
        text,
        "from sniperplug.services.penny_score import score_penny_candidate\n",
        "from sniperplug.services.embed_delivery import send_summary_and_embeds\n"
        "from sniperplug.services.penny_score import score_penny_candidate\n",
        "Home Depot batching import",
    )
    text = replace_once(
        text,
        "            await interaction.followup.send(\n"
        "                embeds=[summary] + batch.embeds[:5],\n"
        "                view=HomeDepotResultView(batch.candidates[:5]),\n"
        "                ephemeral=True,\n"
        "            )\n",
        "            await send_summary_and_embeds(\n"
        "                interaction,\n"
        "                summary=summary,\n"
        "                embeds=batch.embeds[:5],\n"
        "                view=HomeDepotResultView(batch.candidates[:5]),\n"
        "                ephemeral=True,\n"
        "            )\n",
        "Home Depot delivery",
    )
    path.write_text(text)


def patch_open_box() -> None:
    path = Path("sniperplug/cogs/open_box_deals.py")
    text = path.read_text()
    text = replace_once(
        text,
        "from sniperplug.services.candidate_pipeline import evaluate_candidate\n",
        "from sniperplug.services.candidate_pipeline import evaluate_candidate\n"
        "from sniperplug.services.embed_delivery import send_summary_and_card_batches\n",
        "open box batching import",
    )
    text = replace_once(
        text,
        "        await interaction.followup.send(embeds=[summary] + [card.embed for card in cards[:5]], ephemeral=True)\n",
        "        await send_summary_and_card_batches(interaction, summary=summary, cards=cards[:5], ephemeral=True)\n",
        "open box delivery",
    )
    path.write_text(text)


def write_tests() -> None:
    path = Path("tests/test_universal_embed_batching.py")
    path.write_text('''from pathlib import Path\n\nimport discord\n\nfrom sniperplug.services.embed_delivery import (\n    SAFE_EMBED_MESSAGE_LIMIT,\n    batch_embeds_for_limit,\n    embed_text_size,\n    sanitize_embed,\n)\n\n\ndef _rich_embed(index: int, size: int = 1800) -> discord.Embed:\n    embed = discord.Embed(title=f"Deal {index}", description="x" * size)\n    embed.add_field(name="Proof", value="y" * 900, inline=False)\n    return embed\n\n\ndef test_rich_embeds_are_split_below_combined_message_limit():\n    batches = batch_embeds_for_limit([_rich_embed(i) for i in range(5)])\n    assert len(batches) >= 3\n    assert all(sum(embed_text_size(embed) for embed in batch) <= SAFE_EMBED_MESSAGE_LIMIT for batch in batches)\n    assert all(len(batch) <= 10 for batch in batches)\n\n\ndef test_oversized_single_embed_is_sanitized_before_batching():\n    embed = discord.Embed(title="t" * 400, description="d" * 9000)\n    for index in range(30):\n        embed.add_field(name=f"field-{index}" * 40, value="v" * 3000, inline=False)\n    safe = sanitize_embed(embed)\n    assert embed_text_size(safe) <= 5600\n    assert len(safe.fields) <= 25\n    assert len(safe.title or "") <= 256\n    assert len(safe.description or "") <= 4096\n\n\ndef test_all_rich_scan_routes_use_shared_batch_delivery():\n    deal_scanner = Path("sniperplug/cogs/deal_scanner.py").read_text()\n    home_depot = Path("sniperplug/cogs/home_depot_search.py").read_text()\n    open_box = Path("sniperplug/cogs/open_box_deals.py").read_text()\n\n    assert "send_summary_and_card_batches" in deal_scanner\n    assert deal_scanner.count("send_summary_and_card_batches(") >= 6\n    assert "embeds=[summary] + [card.embed for card in shown_cards]" not in deal_scanner\n    assert "embeds=[summary] + batch.embeds[:5]" not in home_depot\n    assert "send_summary_and_embeds(" in home_depot\n    assert "embeds=[summary] + [card.embed for card in cards[:5]]" not in open_box\n    assert "send_summary_and_card_batches(" in open_box\n''')


def main() -> None:
    patch_deal_scanner()
    patch_home_depot()
    patch_open_box()
    write_tests()


if __name__ == "__main__":
    main()
