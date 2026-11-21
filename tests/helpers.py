from __future__ import annotations

import json
from typing import Any


def search_item(
    note_id: str,
    user_id: str,
    nickname: str,
    title: str = "咖啡笔记",
    desc: str = "正文 #咖啡 #手冲",
    liked: int = 100,
    collected: int = 10,
    comments: int = 5,
    shares: int = 1,
) -> dict[str, Any]:
    return {
        "mix_track_id": f"NOTE__{note_id}",
        "model_type": "note",
        "note": {
            "id": note_id,
            "title": title,
            "desc": desc,
            "type": "normal",
            "timestamp": 1_730_000_000,
            "liked_count": liked,
            "collected_count": collected,
            "comments_count": comments,
            "shared_count": shares,
            "user": {
                "userid": user_id,
                "nickname": nickname,
                "red_id": f"red_{user_id}",
                "images": f"https://example.com/{user_id}.jpg",
            },
        },
    }


def search_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    inner = {"code": 0, "data": {"items": items}}
    return {"code": 0, "msg": "ok", "data": json.dumps(inner, ensure_ascii=False)}


def user_payload(
    user_id: str = "u1",
    nickname: str = "咖啡师",
    fans: int = 19360,
    posted: int = 399,
    liked: int = 79178,
    collected: int = 41243,
    verify: str = "咖啡师",
    ip_location: str = "上海",
) -> dict[str, Any]:
    data = {
        "userid": user_id,
        "nickname": nickname,
        "red_id": f"red_{user_id}",
        "fans": fans,
        "follows": 12,
        "liked": liked,
        "collected": collected,
        "note_num_stat": {"posted": posted, "liked": liked, "collected": collected},
        "interactions": [
            {"type": "fans", "count": fans},
            {"type": "interaction", "count": liked + collected},
        ],
        "desc": "咖啡内容创作者",
        "tags": [{"name": "女", "tag_type": "info"}, {"name": "上海", "tag_type": "location"}],
        "images": f"https://example.com/{user_id}.jpg",
        "ip_location": ip_location,
        "red_official_verify_content": verify,
        "share_link": f"https://www.xiaohongshu.com/user/profile/{user_id}",
    }
    envelope = {"code": 0, "data": data}
    return {"code": 0, "msg": "ok", "data": json.dumps(envelope, ensure_ascii=False)}
