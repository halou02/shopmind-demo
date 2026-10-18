from __future__ import annotations

from app.core.content_generator import (
    CHANNEL_KUAISHOU,
    CHANNEL_SHOPPING,
    CHANNEL_VIDEO,
    CHANNELS,
    build_content_package,
)
from tests.test_content_generator import _build_pack, _reports


def test_content_channels_include_four_platforms(tmp_path) -> None:
    directions, understanding, extraction = _reports(_build_pack(tmp_path))
    report = build_content_package(
        directions,
        understanding,
        extraction,
        selected_direction_id=directions.directions[0].direction_id,
        ai_analysis={
            "product_facts": {"颜色": "云苔绿", "材质": "尼龙"},
            "visual_keywords": ["通勤", "托特"],
            "category_positioning": "轻量通勤托特包",
            "source": "offline",
        },
    )
    channels = {item.channel for item in report.items}
    assert len(CHANNELS) == 6
    assert CHANNEL_KUAISHOU in channels
    assert CHANNEL_SHOPPING in channels
    assert CHANNEL_VIDEO in channels
    visual = next(item for item in report.items if item.channel == "visual")
    assert "云苔绿" in visual.body or "AI 视觉要点" in visual.body
