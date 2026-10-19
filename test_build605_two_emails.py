"""Build 6.0.5: first email is list-only; second email contains two attachments."""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from unittest.mock import patch
import scheduled_media_reports as m

class FakeSMTP:
    messages=[]
    def __init__(self,*a,**kw):pass
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def login(self,*a):pass
    def send_message(self,msg,**kw):
        self.messages.append(msg)
        return {}

article=m.Article(canonical_url='https://example.com/real-news', publisher='Publisher', title='ISRO satellite mission update', author='Reporter',published_date='2026-10-19',published_time_ist='09:15:00',published_iso_ist='2026-10-19T09:15:00+05:30',time_known=True,body_excerpt='',institutions=['ISRO'],gate_route='INSTITUTION',support_excerpt='',source_pass=1,discovery_query='',concern_ids=[],tone='Neutral-Mixed',impact_level='Medium',impact_text='SENSITIVE IMPACT TEXT',remarks_action='SENSITIVE ACTION TEXT',section='main')
with TemporaryDirectory() as td:
    docx=Path(td)/'report.docx';pdf=Path(td)/'report.pdf'
    docx.write_bytes(b'word');pdf.write_bytes(b'pdf')
    with patch.object(m,'env',side_effect=lambda k,default='': {'SMTP_EMAIL':'sender@example.com','SMTP_APP_PASSWORD':'test'}.get(k,default)),patch.object(m,'recipients',return_value=['recipient@example.com']),patch.object(m.smtplib,'SMTP_SSL',FakeSMTP):
        first=m.send_news_detection_email('evening',date(2026,10,19),[article])
        second=m.send_edition_email('evening',date(2026,10,19),docx,pdf,articles=[article])
    assert first['sent']==second['sent']==['recipient@example.com']
    assert len(FakeSMTP.messages)==2
    a,b=FakeSMTP.messages
    assert not list(a.iter_attachments())
    assert 'ISRO satellite mission update' in a.get_body(preferencelist=('plain',)).get_content()
    assert 'SENSITIVE IMPACT TEXT' not in a.as_string()
    assert 'SENSITIVE ACTION TEXT' not in a.as_string()
    assert 'https://example.com/real-news' in a.get_body(preferencelist=('html',)).get_content()
    assert len(list(b.iter_attachments()))==2
    assert 'News Detection List' not in b['Subject']
print('Build 6.0.5 two-email regression: PASS')
