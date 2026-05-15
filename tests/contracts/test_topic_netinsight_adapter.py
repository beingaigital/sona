"""topic_netinsight_adapter 字段映射单测（不依赖 opinion-system 与 Playwright）。"""

from __future__ import annotations

from workflow.topic_netinsight_adapter import opinion_row_to_scan_post


def test_opinion_row_to_scan_post_maps_chinese_fields() -> None:
    row = {
        "原始ID": "abc123",
        "URL": "https://example.com/p/1",
        "平台": "微博",
        "作者": "user1",
        "标题": "测试标题",
        "内容": "正文",
        "点赞数": 10,
        "评论数": 3,
        "转发数": 1,
        "情感": "负面",
    }
    post = opinion_row_to_scan_post(row)
    assert post["id"] == "abc123"
    assert post["url"] == "https://example.com/p/1"
    assert post["platform"] == "微博"
    assert post["likes"] == 10
    assert post["comments"] == 3
    assert post["shares"] == 1
    assert post["sentiment"] == "negative"
