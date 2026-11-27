from __future__ import annotations

from typing import Any

from .client import DataFlowClient, RnoteClient

XHS_COST = 45
DOUYIN_COST = 90


class DataFlowAPI:
    """dataflow.apifox.cn 接口封装，参数名与官方文档保持一致。"""

    def __init__(
        self,
        client: DataFlowClient,
        *,
        xhs_cost: int = XHS_COST,
        douyin_cost: int = DOUYIN_COST,
    ) -> None:
        self.client = client
        self.xhs_cost = int(xhs_cost)
        self.douyin_cost = int(douyin_cost)

    # ---- 账户 ----

    def balance(self) -> Any:
        return self.client.get("/user/callremain/count", cost=0)

    # ---- 小红书 V3 ----

    def xhs_search(
        self,
        keyword: str,
        page: int = 1,
        sort_type: str | None = None,
        note_type: str | None = None,
        note_time: str | None = None,
    ) -> Any:
        return self.client.get(
            "/api/xhs/v3/appsearch",
            {"keyword": keyword, "page": page, "sort_type": sort_type, "note_type": note_type, "note_time": note_time},
            cost=self.xhs_cost,
        )

    def xhs_note(self, note_id: str, channel: str = "old") -> Any:
        path = "/api/xhs/v3/notedetailv2" if channel == "new" else "/api/xhs/v3/notedetail"
        return self.client.get(path, {"noteId": note_id}, cost=self.xhs_cost)

    def xhs_note_full(self, note_id: str) -> Any:
        return self.client.get("/api/xhs/v3/fullnotedetail", {"noteId": note_id}, cost=self.xhs_cost)

    def xhs_note_video(self, note_id: str) -> Any:
        return self.client.get("/api/xhs/v3/videonotedetail", {"noteId": note_id}, cost=self.xhs_cost)

    def xhs_comments(self, note_id: str, cursor: str | None = None, sort_strategy: str | None = None) -> Any:
        return self.client.get(
            "/api/xhs/v3/getcomment",
            {"noteId": note_id, "cursor": cursor, "sort_strategy": sort_strategy},
            cost=self.xhs_cost,
        )

    def xhs_sub_comments(self, note_id: str, comment_id: str, cursor: str | None = None) -> Any:
        return self.client.get(
            "/api/xhs/v3/getsubcomments",
            {"noteId": note_id, "commentId": comment_id, "cursor": cursor},
            cost=self.xhs_cost,
        )

    def xhs_user(self, user_id: str) -> Any:
        return self.client.get("/api/xhs/v3/appuserinfo", {"userId": user_id}, cost=self.xhs_cost)

    def xhs_user_posted(self, user_id: str, cursor: str | None = None) -> Any:
        return self.client.get("/api/xhs/v3/appuserposted", {"userId": user_id, "cursor": cursor}, cost=self.xhs_cost)

    def xhs_topic_feed(self, page_id: str, sort: str | None = None, cursor: str | None = None) -> Any:
        return self.client.get(
            "/api/xhs/v3/topicfeed", {"page_id": page_id, "sort": sort, "cursor": cursor}, cost=self.xhs_cost
        )

    # ---- 抖音 ----

    def douyin_video(self, aweme_id: str) -> Any:
        return self.client.get("/api/douyin/videodetail", {"awemeId": aweme_id}, cost=self.douyin_cost)

    def douyin_comments(self, aweme_id: str, cursor: str | None = None) -> Any:
        return self.client.get("/api/douyin/comment", {"awemeId": aweme_id, "cursor": cursor}, cost=self.douyin_cost)

    def douyin_sub_comments(self, aweme_id: str, comment_id: str, cursor: str | None = None) -> Any:
        return self.client.get(
            "/api/douyin/subcomment", {"awemeId": aweme_id, "commentId": comment_id, "cursor": cursor}, cost=self.douyin_cost
        )


class RnoteAPI:
    def __init__(self, client: RnoteClient) -> None:
        self.client = client

    def balance(self) -> Any:
        return self.client.get("/api/v1/account/balance", cost=0)

    def search_notes(
        self,
        keyword: str,
        page: int = 1,
        search_id: str | None = None,
        search_session_id: str | None = None,
        sort_type: str | None = None,
        note_type: str | None = None,
        time_filter: str | None = None,
        path: str = "/api/v2/crawler/search/notes",
        cost: int = 0,
    ) -> Any:
        return self.client.get(
            path,
            {
                "keyword": keyword,
                "page": page,
                "search_id": search_id,
                "search_session_id": search_session_id,
                "sort_type": sort_type,
                "note_type": note_type,
                "time_filter": time_filter,
            },
            cost=cost,
        )

    def user(
        self,
        user_id: str,
        path: str = "/api/v2/crawler/user/info",
        cost: int = 0,
    ) -> Any:
        return self.client.get(path, {"user_id": user_id}, cost=cost)
