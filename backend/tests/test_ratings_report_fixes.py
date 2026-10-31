import json

from app.data_providers import wire_search
from app.services import claude_usage_service, news_link_service
from app.services.rating_service import filing_index_url

NAME_MAP = {"nasdaq": "NDAQ", "aurora innovation": "AUR", "apple": "AAPL"}


def test_exchange_tag_names_its_own_ticker_not_nasdaq_inc():
    t = "NASDAQ: AUR Investigation Alert: Kessler Topaz Encourages Aurora Innovation, Inc. (AUR) Investors to Contact the Firm"
    assert wire_search.ticker_for_headline(t, NAME_MAP) == "AUR"
    assert wire_search.ticker_for_headline("NASDAQ: ZZZZ Something", NAME_MAP) is None  # a ticker the SEC list does not know
    assert wire_search.ticker_for_headline("Nasdaq announces new listing", NAME_MAP) is None  # the exchange itself is not a stock find
    assert wire_search.ticker_for_headline("Apple announces a record quarter", NAME_MAP) == "AAPL"


def test_law_firm_and_routine_notices_are_noise_but_real_news_is_not():
    noise = [
        "AppLovin Deadline: APP Investors with Losses in Excess of $100K Have Opportunity to Lead Securities Fraud Lawsuit",
        "Twilio Investigation Notice: Levi & Korsinsky Notifies Investors",
        "Duolingo Shareholder Alert: ClaimsFiler Reminds Investors With Losses",
        "Adicet Bio Reports Inducement Grant under Nasdaq Listing Rule 5635(c)(4)",
        "PowerBank Announces Results of Annual General Meeting",
    ]
    real = ["Acme Corp awarded $400 million Navy contract", "Acme receives FDA approval for its lead drug",
            "Acme to acquire Beta Inc. in a definitive agreement"]
    assert all(wire_search.is_noise_headline(t) for t in noise)
    assert not any(wire_search.is_noise_headline(t) for t in real)


def test_form4_xml_links_become_the_readable_filing_page():
    xml = "https://www.sec.gov/Archives/edgar/data/320193/000114036126041201/form4.xml"
    assert filing_index_url(xml) == "https://www.sec.gov/Archives/edgar/data/320193/000114036126041201/0001140361-26-041201-index.htm"
    index = "https://www.sec.gov/Archives/edgar/data/1067983/000119312526352200/0001193125-26-352200-index.htm"
    assert filing_index_url(index) == index and filing_index_url("") == ""


def test_finnhub_links_are_resolved_or_dropped_when_gone(monkeypatch):
    news_link_service._cache.clear()
    answers = {"https://finnhub.io/api/news?id=a": "https://publisher.example/a", "https://finnhub.io/api/news?id=b": "",
               "https://finnhub.io/api/news?id=c": "https://finnhub.io/api/news?id=c"}
    monkeypatch.setattr(news_link_service, "_check", lambda u: answers[u])
    items = [{"text": "A", "url": "https://finnhub.io/api/news?id=a"}, {"text": "B", "url": "https://finnhub.io/api/news?id=b"},
             {"text": "C", "url": "https://finnhub.io/api/news?id=c"}, {"text": "D", "url": "https://news.google.com/x"}]
    out = news_link_service.resolve_item_links(items)
    assert [i["url"] for i in out] == ["https://publisher.example/a", "", "https://finnhub.io/api/news?id=c", "https://news.google.com/x"]


def test_usage_reads_the_credentials_file_named_by_the_environment(tmp_path, monkeypatch):
    f = tmp_path / "creds.json"
    f.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-from-file"}}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CREDENTIALS_FILE", str(f))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert claude_usage_service._tokens()[0] == "tok-from-file"
