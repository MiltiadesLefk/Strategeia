"""AI-labelled news in the rating: a rumor never moves points, a firm material story does, and the cap holds."""

from __future__ import annotations

import json

from app.analysis.news_card_scoring import net_news_weight
from app.knowledge.news_cards import news_card_batch_json_schema, parse_card_batch

CARD = {"is_about_this_company": True, "materiality": "high", "sentiment": "positive", "certainty": "confirmed"}


def test_rumor_cards_do_not_count():
    assert net_news_weight([{**CARD, "certainty": "rumor"}]) == (0, 0)
    assert net_news_weight([CARD, {**CARD, "certainty": "rumor"}]) == (2, 1)


def test_conditional_and_old_cards_still_count():
    old = {k: v for k, v in CARD.items() if k != "certainty"}  # a card stored before the field existed
    assert net_news_weight([{**CARD, "certainty": "conditional"}, old]) == (4, 2)


def test_certainty_is_optional_in_a_reply_and_validated_when_given():
    base = {"index": 1, "event_type": "other", "sentiment": "positive", "materiality": "high", "companies_mentioned": [],
            "one_line_summary": "x", "is_about_this_company": True}
    assert parse_card_batch(json.dumps({"cards": [base]})).cards[0].certainty == "unclear"
    assert parse_card_batch(json.dumps({"cards": [{**base, "certainty": "rumor"}]})).cards[0].certainty == "rumor"
    assert parse_card_batch(json.dumps({"cards": [{**base, "certainty": "sure"}]})) is None
    assert "certainty" in news_card_batch_json_schema()["properties"]["cards"]["items"]["properties"]
