"""Offline smoke checks for Build 6.0 V8 rules. No network calls."""
from datetime import date, datetime
from pathlib import Path
import tempfile

import scheduled_media_reports as s

assert s.BUILD == "6.0.6"
assert len(s.THEMES) == 44
assert sum(len(v) for v in s.PASS_QUERIES.values()) >= 39
assert set(s.PASS_QUERIES) == {1, 2, 3, 4, 5}

# Strict edition windows.
start, end, lookback = s.edition_window("morning", date(2026, 10, 19))
assert start.isoformat().startswith("2026-10-18T20:00:00")
assert end.isoformat().startswith("2026-10-19T08:00:00")
assert lookback.isoformat().startswith("2026-10-18T08:00:00")
start2, end2, _ = s.edition_window("evening", date(2026, 10, 19))
assert start2.isoformat().startswith("2026-10-19T08:00:00")
assert end2.isoformat().startswith("2026-10-19T20:00:00")

# V8 tone labels.
assert s.classify_tone("ISRO successfully completes propulsion test", "ISRO successfully completed a key test.", ["ISRO"]) == "Positive"
assert s.classify_tone("ISRO launch postponed by weather", "No technical problem was reported.", ["ISRO"]) == "Neutral-Mixed"
assert s.classify_tone("ISRO faces criticism over repeated delays", "Experts criticised ISRO over programme management.", ["ISRO"]) == "Critical-Negative"

# Consequence-based impact: a minor weather postponement remains low, repeated material delay is >= Medium.
assert s.classify_impact("ISRO launch postponed one day due to weather", ["ISRO"], [])[0] == "Low"
assert s.classify_impact("ISRO faces repeated delays in a programme", ["ISRO"], [])[0] in {"Medium", "High"}

# Semantic validator accepts only V8 labels.
valid = s._validate_semantic_result({
    "crux": "IN-SPACe is encouraging indigenous chips in space systems.",
    "tone": "Positive", "impact_level": "Medium",
    "impact_analysis": "This can strengthen the domestic component supply chain. It may reduce import dependence over time.",
})
assert valid and valid["tone"] == "Positive"

# Seven-column document smoke check with hard Relevant Space page break.
A = s.Article
common = dict(
    author="Reporter", published_date="2026-10-19", published_time_ist="09:15:00",
    published_iso_ist="2026-10-19T09:15:00+05:30", time_known=True,
    body_excerpt="Body", institutions=["ISRO"], gate_route="INSTITUTION",
    support_excerpt="ISRO", source_pass=2, discovery_query="test", concern_ids=[],
    tone="Positive", impact_level="Medium",
    impact_text="This supports a relevant Indian space capability. It may assist future mission work.",
    remarks_action="Nil action.",
)
arts = [
    A(canonical_url="https://example.com/a", publisher="Example Daily", title="ISRO completes a propulsion milestone", section="main", **common),
    A(canonical_url="https://example.com/b", publisher="Science World", title="New satellite technology improves climate observations", section="relevant", **{**common, "institutions": [], "gate_route": "BROADER_SPACE", "tone": "Neutral-Mixed", "impact_level": "Low"}),
]
with tempfile.TemporaryDirectory() as td:
    path = Path(td) / "sample.docx"
    s.build_docx("evening", date(2026, 10, 19), start2, end2, arts, path, generated_at=datetime.fromisoformat("2026-10-19T18:55:23+05:30"))
    assert path.exists()
    assert s.validate_docx_v8(path, has_relevant=True)

print("Build 6.0 V8 offline smoke checks: PASS")
