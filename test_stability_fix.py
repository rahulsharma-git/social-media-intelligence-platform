"""Offline regression tests retained for Build 6.0.6."""
from unittest.mock import patch
import tempfile
from pathlib import Path
from datetime import date, datetime
import requests
import scheduled_media_reports as s

assert s.BUILD == '6.0.6'
s._SEMANTIC_DISABLED_PROVIDERS.clear()
s._SEMANTIC_RESULT_CACHE.clear()
counts = {'groq': 0, 'router': 0}
def rate_limited(_):
    counts['groq'] += 1
    resp = requests.Response(); resp.status_code = 429
    raise requests.HTTPError('429', response=resp)
def fallback(_):
    counts['router'] += 1
    return {'tone':'Neutral-Mixed','impact_level':'Low','crux':'Test','impact_analysis':'No immediate impact.'}
with patch.object(s, '_analyse_with_groq', rate_limited), patch.object(s, '_analyse_with_openrouter', fallback):
    x = s.semantic_article_analysis('One', 'body', 'Paper', ['ISRO'])
    y = s.semantic_article_analysis('Two', 'body', 'Paper', ['ISRO'])
    z = s.semantic_article_analysis('One', 'body', 'Paper', ['ISRO'])
assert counts == {'groq':1, 'router':2}, counts
assert x[1] == y[1] == z[1] == 'openrouter-free'

# Verify non-Latin title succeeds only when source URL annotation is present.
from pypdf import PdfWriter
from pypdf.annotations import Link
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO
from types import SimpleNamespace
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'sample.pdf'
    buf=BytesIO()
    c=canvas.Canvas(buf,pagesize=A4)
    c.drawString(50,760,'description / title of news  publisher / author  publication date / time  tone  impact analysis  remarks / action')
    c.drawString(50,735,'Relevant Space News')
    c.save();buf.seek(0)
    from pypdf import PdfReader
    reader=PdfReader(buf)
    writer=PdfWriter();writer.add_page(reader.pages[0])
    writer.add_annotation(page_number=0,annotation=Link(rect=(50,700,130,720),url='https://example.com/marathi'))
    with open(p,'wb') as f:writer.write(f)
    article=SimpleNamespace(title='इस्रो कडून मोहिमेची चाचणी',canonical_url='https://example.com/marathi',section='relevant')
    s.validate_pdf_matches_articles(p,[article])
    article.canonical_url='https://example.com/missing'
    # Missing PDF annotations must fail: PDF links are mandatory.
    try:
        s.validate_pdf_matches_articles(p,[article])
        raise AssertionError("Missing PDF link accepted")
    except RuntimeError as e:
        assert "clickable source hyperlink missing" in str(e)
    article.canonical_url=''
    try:s.validate_pdf_matches_articles(p,[article]);raise AssertionError('Empty source URL accepted')
    except RuntimeError as e:assert 'no source URL' in str(e)
print('Build 6.0.6 stability tests: PASS')
