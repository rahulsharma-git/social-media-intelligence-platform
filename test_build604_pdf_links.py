"""Regression: PDF annotations are mandatory and repaired at visible source labels."""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date,datetime
from types import SimpleNamespace
from unittest.mock import patch
import scheduled_media_reports as s
from pypdf import PdfWriter, PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO

common=dict(author='Reporter',published_date='2026-10-19',published_time_ist='21:15:00',published_iso_ist='',time_known=True,body_excerpt='',institutions=['ISRO'],gate_route='INSTITUTION',support_excerpt='',source_pass=1,discovery_query='',concern_ids=[],tone='Neutral-Mixed',impact_level='Low',impact_text='A relevant development.',remarks_action='Nil action.',section='main')
article=s.Article(canonical_url='https://example.com/article?x=1&y=2',publisher='News Source',title='Prime Industries Subsidiary Linga Agri Raises First Funding Tranche at Rs 330 Crore',**common)
with TemporaryDirectory() as td:
    td=Path(td)
    docx=td/'report.docx'
    start,end,_=s.edition_window('evening',date(2026,10,19))
    s.build_docx('evening',date(2026,10,19),start,end,[article],docx,generated_at=datetime.fromisoformat('2026-10-19T20:15:00+05:30'))
    assert s.validate_docx_article_links(docx,[article])
    bad=s.Article(**{**vars(article),'canonical_url':'https://example.com/not-in-document'})
    try:
        s.validate_docx_article_links(docx,[bad])
        raise AssertionError('Missing DOCX source URL accepted')
    except RuntimeError as e:
        assert 'DOCX source hyperlink missing' in str(e)
    pdf=s.convert_docx_to_pdf(docx,td)
    s.validate_pdf_matches_articles(pdf,[article])
    # Simulate a converter that produces readable text but strips all annotations.
    stripped=td/'stripped.pdf'
    reader=PdfReader(str(pdf));writer=PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
        if '/Annots' in writer.pages[-1]:del writer.pages[-1]['/Annots']
    with open(stripped,'wb') as f:writer.write(f)
    try:
        s.validate_pdf_matches_articles(stripped,[article])
        raise AssertionError('Missing PDF link accepted')
    except RuntimeError as exc:
        assert 'clickable source hyperlink missing' in str(exc)
    s.ensure_pdf_source_links(stripped,[article])
    s.validate_pdf_matches_articles(stripped,[article])
print('Build 6.0.5 PDF annotation regression: PASS')
