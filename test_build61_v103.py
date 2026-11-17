from datetime import date, datetime
from pathlib import Path
import tempfile

import scheduled_media_reports as sm


def make_article(title, tone, impact, section, hour, url):
    dt = datetime(2026, 11, 17, hour, 0, tzinfo=sm.IST)
    return sm.Article(
        canonical_url=url,
        publisher="Example Publisher",
        title=title,
        author="Reporter",
        published_date=dt.date().isoformat(),
        published_time_ist=dt.strftime("%H:%M:%S"),
        published_iso_ist=dt.isoformat(),
        time_known=True,
        body_excerpt="ISRO space programme article body in English with relevant details.",
        institutions=["ISRO"],
        gate_route="INSTITUTION",
        support_excerpt="ISRO space programme article body.",
        source_pass=1,
        discovery_query="test",
        concern_ids=[],
        tone=tone,
        impact_level=impact,
        impact_text="The article highlights a reported development of potential relevance to the Indian space programme.",
        section=section,
        timestamp_grade="A",
        url_verification_status="publisher article content/title confirmed",
    )


def main():
    audit = sm.load_master_query_contract()
    assert audit["bytes"] > 1000

    ms, me, ml = sm.edition_window("morning", date(2026, 11, 17))
    assert (ms.hour, me.hour) == (18, 6)
    assert ms.date().isoformat() == "2026-11-16" and me.date().isoformat() == "2026-11-17"
    es, ee, el = sm.edition_window("evening", date(2026, 11, 17))
    assert (es.hour, ee.hour) == (6, 18)
    assert (me - ml).total_seconds() == 24 * 3600
    assert (ee - el).total_seconds() == 24 * 3600

    rows = [
        make_article("Neutral high", "Neutral", "High", "main", 5, "https://example.com/n-high"),
        make_article("Positive low", "Positive", "Low", "main", 4, "https://example.com/p-low"),
        make_article("Positive high older", "Positive", "High", "main", 2, "https://example.com/p-high-old"),
        make_article("Positive high newer", "Positive", "High", "main", 3, "https://example.com/p-high-new"),
        make_article("Startup medium", "Positive", "Medium", "startup", 5, "https://example.com/startup"),
        make_article("Relevant negative", "Negative", "Low", "relevant", 5, "https://example.com/relevant"),
    ]
    ordered = sm.sort_and_number_articles(rows)
    assert [x.title for x in ordered[:4]] == ["Positive high newer", "Positive high older", "Positive low", "Neutral high"]
    assert [x.final_no for x in ordered] == list(range(1, len(ordered) + 1))
    assert ordered[4].decision == "Space Startups"

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        docx = td / "test.docx"
        xlsx = td / "test.xlsx"
        sm.build_docx("morning", date(2026, 11, 17), ms, me, ordered, docx, generated_at=datetime(2026,11,17,6,20,30,tzinfo=sm.IST))
        sm.validate_docx_v10_3(docx, ordered)
        sm.validate_docx_article_links(docx, ordered)
        sm.build_xlsx_source_register(ordered, xlsx, ms, me)
        sm.validate_xlsx_source_register(xlsx, ordered)
        assert docx.exists() and xlsx.exists()

    plain, html = sm.format_email_article_list(ordered)
    assert "Source URL: https://example.com/p-high-new" in plain
    assert "Impact" not in plain and "Tone" not in plain and "Suggested action" not in plain
    assert "https://example.com/p-high-new" in html

    print("Build 6.1.1 / Master Query V10.3 offline checks: PASS")


if __name__ == "__main__":
    main()
