import pytest

from app.reference_standard import defaults
from app.report_runner import writing_prompt


@pytest.mark.parametrize(
    "preset,cover,toc,uses_toc",
    [
        ("appendix-1", "ORIGINAL", "ORIGINAL", False),
        ("appendix-2", "ORIGINAL", "ORIGINAL", True),
        ("appendix-3", "ORIGINAL", "ORIGINAL", True),
        ("appendix-1", "ON", "NONE", False),
        ("appendix-2", "ON", "NONE", False),
        ("appendix-1", "OFF", "SEPARATE", True),
        ("appendix-1", "OFF", "COVER", True),
        ("appendix-3", "OFF", "ORIGINAL", True),
    ],
)
def test_toc_writing_limit_tracks_selected_toc_independently_of_cover(preset, cover, toc, uses_toc):
    settings = {**defaults(), "preset_id": preset, "cover_mode": cover, "toc_mode": toc}
    prompt = writing_prompt({"visual_standard": {"revision": 3, "settings": settings}})
    assert ("automatic TOC limit of 30 headings" in prompt) is uses_toc
