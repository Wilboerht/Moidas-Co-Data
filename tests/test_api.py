from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from codata import api_server
from codata.auth import hash_password
from codata.config import Settings
from codata.db import Database
from codata.normalize import normalize_user_info
from helpers import user_payload


class ApiTests(unittest.TestCase):
    PROVIDER_ENV_KEYS = (
        "EVERYINFRA_API_KEY",
        "TIKHUB_API_KEY",
        "JUSTONEAPI_TOKEN",
        "SOCIALDATAX_API_KEY",
        "REDFOX_API_KEY",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.provider_env = {key: os.environ.pop(key, None) for key in self.PROVIDER_ENV_KEYS}
        self.settings = Settings(host="", token="", output_dir=Path(self.tmp.name))
        self.old_settings = api_server.settings
        api_server.settings = self.settings

        def override_db():
            db = Database(self.settings.output_dir)
            try:
                yield db
            finally:
                db.close()

        api_server.app.dependency_overrides[api_server.get_db] = override_db
        self.client = TestClient(api_server.app, base_url="http://127.0.0.1")

        with Database(self.settings.output_dir) as db:
            self.round_id = db.create_round(
                ["咖啡"],
                pages=1,
                max_creators=None,
                name="API-TEST",
                category="咖啡",
                categories={"咖啡": "咖啡"},
            )
            db.upsert_creator(normalize_user_info(user_payload("u1", "咖啡师", fans=19360)))
            db.upsert_note(
                {
                    "platform": "xhs",
                    "note_id": "n1",
                    "author_id": "u1",
                    "title": "咖啡笔记",
                    "desc": "正文 #咖啡",
                    "note_type": "normal",
                    "published_at": 1_730_000_000,
                    "liked": 100,
                    "collected": 10,
                    "comments": 5,
                    "shares": 1,
                    "url": "https://www.xiaohongshu.com/explore/n1",
                    "hashtags": json.dumps(["咖啡"], ensure_ascii=False),
                    "raw_json": "{}",
                }
            )
            db.add_note_hit(self.round_id, "xhs", "n1", "咖啡", 1, 1)
            db.refresh_creator_rounds(self.round_id)
            db.create_user("test-admin", hash_password("test-password-123"), role="admin")

        login = self.client.post(
            "/api/auth/login",
            json={"username": "test-admin", "password": "test-password-123"},
        )
        self.assertEqual(login.status_code, 200)
        self.csrf_token = login.json()["csrf_token"]
        self.user_id = login.json()["user"]["id"]
        self.client.headers.update({"X-CSRF-Token": self.csrf_token})

    def tearDown(self) -> None:
        api_server.app.dependency_overrides.clear()
        api_server.settings = self.old_settings
        for key, value in self.provider_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_health_and_lists(self) -> None:
        health = self.client.get("/api/health").json()
        self.assertTrue(health["ok"])
        self.assertEqual(health["database"], "ok")
        rounds = self.client.get("/api/rounds").json()
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["category"], "咖啡")
        self.assertEqual(rounds[0]["creators"], 1)
        self.assertIn("job", rounds[0])
        self.assertEqual(rounds[0]["status_label"], "已完成" if rounds[0]["status"] == "done" else rounds[0]["status_label"])
        keywords = self.client.get("/api/keywords").json()
        self.assertEqual(keywords[0]["word"], "咖啡")

    def test_creator_endpoints(self) -> None:
        payload = self.client.get(f"/api/rounds/{self.round_id}/creators").json()
        self.assertEqual(payload["total"], 1)
        creators = payload["items"]
        self.assertEqual(len(creators), 1)
        creator = creators[0]
        for field in (
            "nickname",
            "avatar",
            "fans",
            "follows",
            "collected",
            "fans_wan",
            "fans_tier",
            "posted",
            "liked",
            "interaction",
            "tags",
            "profile_url",
            "user_id",
            "account_id",
            "round_name",
            "data_round",
            "top_title",
            "top_url",
            "top_liked",
            "source_keywords",
            "source_labels",
            "matched_keywords",
            "source",
            "platform_label",
            "owner_user_id",
            "owner_username",
            "claimed_at",
            "cooperation_status",
            "cooperation_status_label",
            "cooperation_updated_at",
            "cooperation_platform",
            "raw_title",
            "remark",
            "sample_notes",
            "sample_collected",
            "sample_comments",
        ):
            self.assertIn(field, creator)

        any_keyword = self.client.get(
            f"/api/rounds/{self.round_id}/creators",
            params={"source": "咖啡,手冲"},
        ).json()
        self.assertEqual(any_keyword["total"], 1)
        all_keywords = self.client.get(
            f"/api/rounds/{self.round_id}/creators",
            params={"source": "咖啡,手冲", "source_mode": "all"},
        ).json()
        self.assertEqual(all_keywords["total"], 0)
        any_export = self.client.get(
            f"/api/rounds/{self.round_id}/export",
            params={"format": "csv", "source": "咖啡,手冲"},
        )
        self.assertEqual(
            len([line for line in any_export.text.splitlines() if line.strip()]), 2
        )
        all_export = self.client.get(
            f"/api/rounds/{self.round_id}/export",
            params={"format": "csv", "source": "咖啡,手冲", "source_mode": "all"},
        )
        self.assertEqual(
            len([line for line in all_export.text.splitlines() if line.strip()]), 1
        )

        detail = self.client.get(
            f"/api/rounds/{self.round_id}/creators/xhs/u1"
        ).json()
        self.assertEqual(detail["notes"][0]["note_id"], "n1")

        patched = self.client.patch(
            f"/api/rounds/{self.round_id}/creators/xhs/u1",
            json={"status": "shortlisted", "match_direction": "咖啡", "remark": "测试"},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["status"], "shortlisted")
        self.assertEqual(patched.json()["status_label"], "备选")

        batch = self.client.post(
            f"/api/rounds/{self.round_id}/creators/batch",
            json={
                "items": [{"platform": "xhs", "user_id": "u1"}],
                "status": "pending",
            },
        )
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(batch.json()["changed"], 1)

    def test_source_label_filter_and_search(self) -> None:
        with Database(self.settings.output_dir) as db:
            with db._conn:
                db._conn.execute(
                    "UPDATE creator_rounds SET source_labels = ? "
                    "WHERE round_id = ? AND platform = 'xhs' AND user_id = 'u1'",
                    (json.dumps(["投放A", "来源"], ensure_ascii=False), self.round_id),
                )
            db.rebuild_search_index()

        facets = self.client.get(
            f"/api/rounds/{self.round_id}/facets"
        ).json()
        self.assertIn(
            "投放A", {item["value"] for item in facets["labels"]}
        )
        filtered = self.client.get(
            f"/api/rounds/{self.round_id}/creators",
            params={"label": "投放A"},
        ).json()
        self.assertEqual(filtered["total"], 1)
        all_filtered = self.client.get(
            "/api/creators", params={"label": "投放A"}
        ).json()
        self.assertEqual(all_filtered["total"], 1)
        searched = self.client.get(
            f"/api/rounds/{self.round_id}/creators",
            params={"search": "投放A"},
        ).json()
        self.assertEqual(searched["total"], 1)
        short_search = self.client.get(
            f"/api/rounds/{self.round_id}/creators",
            params={"search": "来源"},
        ).json()
        self.assertEqual(short_search["total"], 1)

    def test_creator_claim_and_release(self) -> None:
        url = f"/api/rounds/{self.round_id}/creators/xhs/u1"
        claimed = self.client.post(f"{url}/claim")
        self.assertEqual(claimed.status_code, 200)
        self.assertEqual(claimed.json()["owner_user_id"], self.user_id)
        self.assertEqual(claimed.json()["owner_username"], "test-admin")
        self.assertIsNotNone(claimed.json()["claimed_at"])

        mine = self.client.get(
            f"/api/rounds/{self.round_id}/creators?owner={self.user_id}"
        ).json()
        self.assertEqual(mine["total"], 1)
        unassigned = self.client.get(
            f"/api/rounds/{self.round_id}/creators?owner=unassigned"
        ).json()
        self.assertEqual(unassigned["total"], 0)

        claimed_export = self.client.get(
            f"/api/rounds/{self.round_id}/export?format=csv&owner={self.user_id}"
        )
        text = claimed_export.content.decode("utf-8-sig")
        self.assertIn("负责人", text)
        self.assertEqual(len([line for line in text.splitlines() if line.strip()]), 2)
        unassigned_export = self.client.get(
            f"/api/rounds/{self.round_id}/export?format=csv&owner=unassigned"
        )
        text = unassigned_export.content.decode("utf-8-sig")
        self.assertEqual(len([line for line in text.splitlines() if line.strip()]), 1)

        bulk = self.client.post(
            f"/api/rounds/{self.round_id}/creators/claim",
            json={"items": [{"platform": "xhs", "user_id": "u1"}]},
        )
        self.assertEqual(bulk.status_code, 200)
        self.assertEqual(bulk.json()["already_mine"], 1)

        released = self.client.post(f"{url}/release")
        self.assertEqual(released.status_code, 200)
        self.assertIsNone(released.json()["owner_user_id"])
        self.assertEqual(released.json()["owner_username"], "")

    def test_round_fields_hidden_without_collect_permission(self) -> None:
        with Database(self.settings.output_dir) as db:
            db.create_user("viewer", hash_password("viewer-password-123"), role="member")
        created_lead = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "轮次线索",
                "source_round_id": self.round_id,
            },
        )
        self.assertEqual(created_lead.status_code, 200, created_lead.text)
        lead_id = created_lead.json()["id"]
        self.client.post("/api/auth/logout")
        login = self.client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "viewer-password-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)

        rounds = self.client.get("/api/rounds").json()
        self.assertTrue(rounds)
        self.assertTrue(all(item["name"] == "" for item in rounds))
        self.assertTrue(all(item["job"] is None for item in rounds))
        self.assertIsNone(self.client.get("/api/jobs/latest").json())
        self.assertIsNone(self.client.get("/api/jobs/active").json())
        self.assertIsNone(self.client.get(f"/api/rounds/{self.round_id}/job").json())

        creators = self.client.get("/api/creators").json()
        self.assertEqual(creators["items"][0]["round_name"], "")
        self.assertEqual(creators["items"][0]["data_round"], "")
        detail = self.client.get(f"/api/rounds/{self.round_id}/creators/xhs/u1").json()
        self.assertEqual(detail["round_name"], "")
        self.assertEqual(detail["data_round"], "")

        lead = self.client.get(f"/api/leads/{lead_id}").json()
        self.assertEqual(lead["source_round_name"], "")
        self.assertTrue(lead["rounds"])
        self.assertEqual(lead["rounds"][0]["round_name"], "")
        listed = self.client.get("/api/leads").json()
        self.assertEqual(listed["items"][0]["source_round_name"], "")

        dashboard = self.client.get(f"/api/dashboard?round_id={self.round_id}").json()
        self.assertIsNone(dashboard["round"])
        self.assertNotIn("cost", dashboard["metrics"])
        self.assertEqual(dashboard["counts"]["rounds"], 0)
        viewer_settings = self.client.get("/api/settings").json()
        self.assertEqual(viewer_settings["platforms"], [])
        self.assertEqual(viewer_settings["providers"], [])
        self.assertFalse(viewer_settings["token_configured"])
        self.assertEqual(self.client.get("/api/keywords").status_code, 403)
        self.assertEqual(self.client.get("/api/leads/facets").json()["rounds"], [])

        self.assertEqual(self.client.get(f"/api/rounds/{self.round_id}/calls").status_code, 403)
        self.assertEqual(self.client.get(f"/api/rounds/{self.round_id}/log").status_code, 403)
        self.assertEqual(
            self.client.get(f"/api/rounds/{self.round_id}/export").status_code, 403
        )

    def test_lead_owner_authorization(self) -> None:
        created_user = self.client.post(
            "/api/users",
            json={
                "username": "lead-member",
                "password": "lead-password-123",
                "role": "member",
                "can_collect": True,
                "can_edit_leads": True,
            },
        )
        self.assertEqual(created_user.status_code, 200, created_user.text)
        member_id = created_user.json()["id"]
        lead = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "管理员负责的线索",
                "source_round_id": self.round_id,
            },
        )
        self.assertEqual(lead.status_code, 200, lead.text)
        lead_id = lead.json()["id"]
        self.assertEqual(lead.json()["owner_user_id"], self.user_id)

        self.client.post("/api/auth/logout")
        login = self.client.post(
            "/api/auth/login",
            json={"username": "lead-member", "password": "lead-password-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        self.assertEqual(
            self.client.patch(
                f"/api/leads/{lead_id}",
                json={"nickname": "越权修改"},
                headers=headers,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                f"/api/leads/{lead_id}/assign",
                json={"owner_user_id": member_id},
                headers=headers,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                f"/api/leads/{lead_id}/convert",
                json={"account_id": "u1", "fetch": False},
                headers=headers,
            ).status_code,
            403,
        )

        self.client.post("/api/auth/logout", headers=headers)
        admin = self.client.post(
            "/api/auth/login",
            json={"username": "test-admin", "password": "test-password-123"},
        )
        admin_headers = {"X-CSRF-Token": admin.json()["csrf_token"]}
        assigned = self.client.post(
            f"/api/leads/{lead_id}/assign",
            json={"owner_user_id": member_id},
            headers=admin_headers,
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["owner_user_id"], member_id)

    def test_lead_create_assign_and_convert(self) -> None:
        created = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "API lead",
                "account_id": "u1",
                "profile_url": "",
                "raw_title": "lead",
                "source": "manual",
                "source_round_id": self.round_id,
            },
        )
        self.assertEqual(created.status_code, 200)
        lead = created.json()
        self.assertEqual(lead["status"], "unverified")
        lead_id = lead["id"]

        listed = self.client.get("/api/leads?status=unverified")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)

        assigned = self.client.post(f"/api/leads/{lead_id}/assign", json={"owner_user_id": 1})
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.json()["owner_user_id"], 1)

        converted = self.client.post(
            f"/api/leads/{lead_id}/convert",
            json={"account_id": "u1", "profile_url": "", "fetch": False},
        )
        self.assertEqual(converted.status_code, 200, converted.text)
        self.assertEqual(converted.json()["lead"]["status"], "converted")
        self.assertEqual(converted.json()["creator"]["user_id"], "u1")

    def test_lead_dedup_and_auto_match(self) -> None:
        first = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "线索昵称",
                "account_id": "u1",
                "source": "manual",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        payload = first.json()
        self.assertTrue(payload["created"])
        self.assertEqual(payload["status"], "unverified")
        self.assertEqual(payload["matched_user_id"], "u1")
        self.assertEqual(payload["matched_round_id"], self.round_id)
        self.assertEqual(payload["nickname"], "咖啡师")
        lead_id = payload["id"]

        second = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "另一个昵称",
                "account_id": "u1",
                "source": "manual",
            },
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(second.json()["created"])
        self.assertEqual(second.json()["id"], lead_id)

        bulk = self.client.post(
            "/api/leads/bulk",
            json={
                "items": [
                    {
                        "platform": "xhs",
                        "nickname": "重复线索",
                        "account_id": "u1",
                        "source": "manual",
                    },
                    {
                        "platform": "xhs",
                        "nickname": "新线索",
                        "account_id": "missing-user",
                        "source": "manual",
                    },
                ]
            },
        )
        self.assertEqual(bulk.status_code, 200, bulk.text)
        self.assertEqual(bulk.json()["created"], 1)
        self.assertEqual(bulk.json()["existing"], 1)
        self.assertEqual(bulk.json()["existing_ids"], [lead_id])

        rematch = self.client.post(f"/api/leads/{lead_id}/match")
        self.assertEqual(rematch.status_code, 200, rematch.text)
        self.assertEqual(rematch.json()["match"]["matched"], 1)

        health = self.client.get("/api/leads/identity-health")
        self.assertEqual(health.status_code, 200, health.text)
        self.assertTrue(health.json()["account_index"])
        self.assertEqual(health.json()["duplicate_account_groups"], 0)

        first_revive = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "待恢复线索",
                "account_id": "revive-user",
                "source": "manual",
            },
        )
        self.assertTrue(first_revive.json()["created"])
        revive_id = first_revive.json()["id"]
        discarded = self.client.patch(
            f"/api/leads/{revive_id}", json={"status": "discarded"}
        )
        self.assertEqual(discarded.status_code, 200, discarded.text)
        revived = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "恢复后的线索",
                "account_id": "revive-user",
                "source": "customer",
            },
        )
        self.assertEqual(revived.status_code, 200, revived.text)
        self.assertFalse(revived.json()["created"])
        self.assertTrue(revived.json()["revived"])
        self.assertEqual(revived.json()["id"], revive_id)
        self.assertEqual(revived.json()["status"], "unverified")
        self.assertEqual(revived.json()["nickname"], "恢复后的线索")

    def test_active_job_endpoint(self) -> None:
        with Database(self.settings.output_dir) as db:
            job_id = db.claim_job(self.round_id)
        response = self.client.get("/api/jobs/active")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], job_id)
        self.assertEqual(response.json()["round_id"], self.round_id)
        self.assertEqual(response.json()["active_count"], 1)
        latest = self.client.get("/api/jobs/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["id"], job_id)
        cancelled = self.client.post(f"/api/jobs/{job_id}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertIsNone(self.client.get("/api/jobs/active").json())
        latest = self.client.get("/api/jobs/latest").json()
        self.assertEqual(latest["status"], "cancelled")
        self.assertEqual(latest["active_count"], 0)

    def test_pagination_facets_and_meta(self) -> None:
        page = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&page=1&page_size=50"
        ).json()
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["page"], 1)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["status_label"], "待审核")

        facets = self.client.get(
            f"/api/rounds/{self.round_id}/facets?platform=xhs"
        ).json()
        self.assertEqual(facets["platforms"][0]["value"], "xhs")
        self.assertIn("statuses", facets)
        self.assertIn("cooperation_statuses", facets)
        self.assertIn("tiers", facets)
        self.assertIn("labels", facets)

        meta = self.client.get("/api/meta").json()
        codes = {item["code"] for item in meta["statuses"]}
        self.assertIn("pending", codes)
        self.assertIn("adopted", codes)
        self.assertNotIn("recommended", codes)
        cooperation_codes = {item["code"] for item in meta["cooperation_statuses"]}
        self.assertIn("not_contacted", cooperation_codes)
        self.assertIn("completed", cooperation_codes)

    def test_fts_search(self) -> None:
        found = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&search=咖啡师"
        ).json()
        self.assertEqual(found["total"], 1)
        missing = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&search=不存在的人"
        ).json()
        self.assertEqual(missing["total"], 0)

    def test_export_uses_read_snapshot(self) -> None:
        with Database(self.settings.output_dir) as db:
            db.upsert_creator(normalize_user_info(user_payload("u2", "第二位", fans=5000)))
            db.add_creator_round(self.round_id, "xhs", "u2")
        stream_db = Database(self.settings.output_dir)
        stream = api_server._iter_export_items(
            stream_db,
            None,
            {},
            page_size=1,
        )
        try:
            first = next(stream)
            self.assertEqual(first["user_id"], "u1")
            with Database(self.settings.output_dir) as db:
                db.upsert_creator(normalize_user_info(user_payload("u3", "第三位", fans=30000)))
                db.add_creator_round(self.round_id, "xhs", "u3")
            remaining = [first, *list(stream)]
            self.assertEqual({item["user_id"] for item in remaining}, {"u1", "u2"})
        finally:
            stream.close()
            stream_db.close()

    def test_filtered_export(self) -> None:
        self.client.patch(
            f"/api/rounds/{self.round_id}/creators/xhs/u1",
            json={"status": "shortlisted"},
        )
        response = self.client.get(
            f"/api/rounds/{self.round_id}/export?format=csv&scope=filtered"
            "&platform=xhs&status=shortlisted"
        )
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8-sig")
        self.assertIn("昵称", text)
        self.assertIn("咖啡师", text)
        self.assertEqual(len([line for line in text.splitlines() if line.strip()]), 2)

    def test_dashboard_and_export(self) -> None:
        dashboard = self.client.get(f"/api/dashboard?round_id={self.round_id}").json()
        self.assertEqual(dashboard["metrics"]["creators"], 1)
        self.assertEqual(dashboard["metrics"]["enriched"], 1)
        self.assertIn("cooperation_counts", dashboard)
        self.assertIn("cooperation_metrics", dashboard)
        self.assertIn("cooperation_stale_days", dashboard)

        all_dashboard = self.client.get("/api/dashboard").json()
        self.assertIsNone(all_dashboard["round"])
        self.assertEqual(all_dashboard["metrics"]["creators"], 1)
        self.assertIn("cooperation_counts", all_dashboard)

        queue = self.client.get(
            f"/api/dashboard/queue?round_id={self.round_id}&scope=all"
        ).json()
        self.assertEqual(queue["counts"]["all"], 1)
        self.assertEqual(queue["counts"]["pending"], 1)
        self.assertEqual(queue["counts"]["unclaimed"], 1)
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(queue["items"][0]["nickname"], "咖啡师")

        efficiency = self.client.get("/api/dashboard/efficiency?limit=6").json()
        self.assertEqual(len(efficiency["items"]), 1)
        self.assertEqual(efficiency["items"][0]["round_id"], self.round_id)
        self.assertEqual(efficiency["items"][0]["creators"], 1)
        self.assertEqual(efficiency["items"][0]["adopted"], 0)
        self.assertEqual(efficiency["totals"]["creators"], 1)

        activity = self.client.get(
            "/api/dashboard/activity?days=30&tz_offset=480"
        ).json()
        self.assertEqual(activity["days"], 30)
        self.assertEqual(len(activity["items"]), 30)
        self.assertGreaterEqual(activity["totals"]["collected"], 1)
        self.assertGreaterEqual(activity["totals"]["processed"], 0)

        csv_response = self.client.get(f"/api/rounds/{self.round_id}/export?format=csv")
        self.assertEqual(csv_response.status_code, 200)
        text = csv_response.content.decode("utf-8-sig")
        self.assertIn("昵称", text)
        self.assertIn("主页URL", text)
        self.assertIn("账号ID", text)
        self.assertIn("合作状态", text)
        self.assertIn("合作更新时间", text)

        xlsx_response = self.client.get(f"/api/rounds/{self.round_id}/export?format=xlsx")
        self.assertEqual(xlsx_response.status_code, 200)
        self.assertGreater(len(xlsx_response.content), 100)

    def test_review_visibility_and_queue_filters(self) -> None:
        with Database(self.settings.output_dir) as db:
            db.create_user("test-member", hash_password("test-password-123"), role="member")

        def login_as(username: str) -> int:
            self.client.cookies.clear()
            response = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": "test-password-123"},
            )
            self.assertEqual(response.status_code, 200)
            self.client.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
            return int(response.json()["user"]["id"])

        creator_url = f"/api/rounds/{self.round_id}/creators/xhs/u1"

        member_id = login_as("test-member")
        self.assertEqual(self.client.post(f"{creator_url}/claim").status_code, 200)

        login_as("test-admin")
        admin_actionable = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&needs_action=true"
        ).json()
        self.assertEqual(admin_actionable["total"], 0)
        login_as("test-member")
        member_actionable = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&needs_action=true"
        ).json()
        self.assertEqual(member_actionable["total"], 1)
        login_as("test-admin")
        personal_queue = self.client.get(
            f"/api/dashboard/queue?round_id={self.round_id}&scope=all"
        ).json()
        self.assertEqual(personal_queue["counts"]["all"], 0)
        team_queue = self.client.get(
            f"/api/dashboard/queue?round_id={self.round_id}&scope=all&include_others=true"
        ).json()
        self.assertEqual(team_queue["counts"]["all"], 1)
        ratio_queue = self.client.get(
            f"/api/dashboard/queue?round_id={self.round_id}&scope=all&include_others=true&sort=ratio"
        ).json()
        self.assertEqual(ratio_queue["counts"]["all"], 1)
        self.assertEqual(self.client.post(f"{creator_url}/release").status_code, 200)
        self.assertEqual(self.client.post(f"{creator_url}/claim").status_code, 200)

        login_as("test-member")
        shared_review = self.client.patch(f"{creator_url}", json={"status": "shortlisted"})
        self.assertEqual(shared_review.status_code, 200)
        self.assertEqual(shared_review.json()["owner_user_id"], self.user_id)

        login_as("test-admin")
        self.assertEqual(self.client.post(f"{creator_url}/release").status_code, 200)

        login_as("test-member")
        unclaimed_review = self.client.patch(f"{creator_url}", json={"status": "shortlisted"})
        self.assertEqual(unclaimed_review.status_code, 200)
        self.assertEqual(unclaimed_review.json()["owner_user_id"], member_id)
        actionable = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&needs_action=true&sort=sample_average"
        ).json()
        self.assertEqual(actionable["total"], 0)

        login_as("test-admin")
        self.assertEqual(self.client.post(f"{creator_url}/release").status_code, 200)
        current = self.client.get(
            f"/api/dashboard/queue?round_id={self.round_id}&scope=all&include_others=true"
        ).json()["items"][0]["updated_at"]
        first = self.client.patch(
            f"{creator_url}",
            json={
                "status": "adopted",
                "expected_updated_at": current,
                "expected_status": "shortlisted",
            },
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["owner_user_id"], self.user_id)
        stale = self.client.patch(
            f"{creator_url}",
            json={
                "status": "rejected",
                "expected_updated_at": current,
                "expected_status": "shortlisted",
            },
        )
        self.assertEqual(stale.status_code, 409)

    def test_creator_owner_transfer_permissions(self) -> None:
        with Database(self.settings.output_dir) as db:
            member_a = db.create_user("member-a", hash_password("test-password-123"), role="member")
            member_b = db.create_user("member-b", hash_password("test-password-123"), role="member")

        def login_as(username: str) -> int:
            self.client.cookies.clear()
            response = self.client.post(
                "/api/auth/login",
                json={"username": username, "password": "test-password-123"},
            )
            self.assertEqual(response.status_code, 200)
            self.client.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
            return int(response.json()["user"]["id"])

        creator_url = f"/api/rounds/{self.round_id}/creators/xhs/u1"

        login_as("member-a")
        unclaimed_transfer = self.client.post(
            f"{creator_url}/assign", json={"owner_user_id": member_b}
        )
        self.assertEqual(unclaimed_transfer.status_code, 403)

        auto_claimed = self.client.patch(f"{creator_url}", json={"status": "shortlisted"})
        self.assertEqual(auto_claimed.status_code, 200)
        self.assertEqual(auto_claimed.json()["owner_user_id"], member_a)

        transferred = self.client.post(
            f"{creator_url}/assign", json={"owner_user_id": member_b}
        )
        self.assertEqual(transferred.status_code, 200)
        self.assertEqual(transferred.json()["owner_user_id"], member_b)

        forbidden = self.client.post(
            f"{creator_url}/assign", json={"owner_user_id": member_a}
        )
        self.assertEqual(forbidden.status_code, 403)

        login_as("test-admin")
        reassigned = self.client.post(
            f"{creator_url}/assign", json={"owner_user_id": member_a}
        )
        self.assertEqual(reassigned.status_code, 200)
        self.assertEqual(reassigned.json()["owner_user_id"], member_a)
        released = self.client.post(f"{creator_url}/assign", json={"owner_user_id": None})
        self.assertEqual(released.status_code, 200)
        self.assertIsNone(released.json()["owner_user_id"])

        owners = self.client.get("/api/creators/owners").json()
        usernames = {owner["username"] for owner in owners}
        self.assertIn("member-a", usernames)
        self.assertIn("member-b", usernames)
        self.assertIn("test-admin", usernames)

        batch_items = [{"platform": "xhs", "user_id": "u1"}]
        login_as("member-a")
        forbidden_batch = self.client.post(
            f"/api/rounds/{self.round_id}/creators/batch",
            json={
                "items": batch_items,
                "set_owner": True,
                "owner_user_id": member_b,
            },
        )
        self.assertEqual(forbidden_batch.status_code, 403)

        login_as("test-admin")
        batch_assigned = self.client.post(
            f"/api/rounds/{self.round_id}/creators/batch",
            json={
                "items": batch_items,
                "set_owner": True,
                "owner_user_id": member_b,
            },
        )
        self.assertEqual(batch_assigned.status_code, 200)
        self.assertEqual(batch_assigned.json()["changed"], 1)
        detail = self.client.get(creator_url).json()
        self.assertEqual(detail["owner_user_id"], member_b)

        batch_released = self.client.post(
            f"/api/rounds/{self.round_id}/creators/batch",
            json={
                "items": batch_items,
                "set_owner": True,
                "owner_user_id": None,
            },
        )
        self.assertEqual(batch_released.status_code, 200)
        self.assertIsNone(self.client.get(creator_url).json()["owner_user_id"])

    def test_follow_up_persists_across_new_rounds_and_profile_updates(self) -> None:
        creator_url = f"/api/rounds/{self.round_id}/creators/xhs/u1"
        claimed = self.client.patch(f"{creator_url}", json={"status": "shortlisted"})
        self.assertEqual(claimed.status_code, 200)
        self.assertEqual(claimed.json()["owner_user_id"], self.user_id)

        with Database(self.settings.output_dir) as db:
            new_round = db.create_round(
                ["咖啡"],
                pages=1,
                max_creators=None,
                name="FOLLOW-UP-NEW-ROUND",
                category="咖啡",
                categories={"咖啡": "咖啡"},
            )
            db.add_creator_round(new_round, "xhs", "u1")
            db.upsert_creator(normalize_user_info(user_payload("u1", "咖啡师", fans=20000)))

        new_detail = self.client.get(
            f"/api/rounds/{new_round}/creators/xhs/u1"
        ).json()
        self.assertEqual(new_detail["owner_user_id"], self.user_id)

        all_rows = self.client.get(
            "/api/creators?platform=xhs&search=咖啡师"
        ).json()["items"]
        latest = next(row for row in all_rows if row["user_id"] == "u1")
        self.assertEqual(latest["owner_user_id"], self.user_id)

    def test_cooperation_status_api_and_batch_conflicts(self) -> None:
        new_round = self.round_id
        response = self.client.patch(
            f"/api/rounds/{new_round}/creators/xhs/u1",
            json={
                "status": "adopted",
                "expected_status": "pending",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["cooperation_status"], "not_contacted")
        self.assertEqual(payload["round_status"], "adopted")
        self.assertTrue(payload["cooperation_history"])
        self.assertEqual(
            payload["cooperation_history"][0]["to_status"], "not_contacted"
        )
        self.assertTrue(payload["cooperation_history"][0]["auto"])

        all_payload = self.client.get("/api/creators?platform=xhs").json()
        self.assertEqual(all_payload["items"][0]["status"], "adopted")
        self.assertEqual(
            all_payload["items"][0]["cooperation_status"], "not_contacted"
        )
        self.assertTrue(all_payload["items"][0]["updated_at"])
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(dashboard["metrics"]["adopted"], 1)
        self.assertEqual(dashboard["cooperation_counts"]["not_contacted"], 1)

        updated = self.client.patch(
            "/api/creators/xhs/u1/cooperation",
            json={"status": "contacted", "expected_status": "not_contacted"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["cooperation_status"], "contacted")
        self.assertEqual(updated.json()["cooperation_status_label"], "已联系")

        filtered = self.client.get(
            "/api/creators?platform=xhs&cooperation=contacted"
        ).json()
        self.assertEqual(filtered["total"], 1)
        none_filtered = self.client.get(
            "/api/creators?platform=xhs&cooperation=none"
        ).json()
        self.assertEqual(none_filtered["total"], 0)

        conflict = self.client.post(
            "/api/creators/cooperation/batch",
            json={
                "status": "cooperating",
                "items": [{
                    "platform": "xhs",
                    "user_id": "u1",
                    "expected_status": "not_contacted",
                }],
            },
        )
        self.assertEqual(conflict.status_code, 200, conflict.text)
        self.assertEqual(conflict.json()["changed"], 0)
        self.assertEqual(len(conflict.json()["conflicts"]), 1)
        self.assertEqual(
            self.client.get("/api/creators?platform=xhs").json()["items"][0][
                "cooperation_status"
            ],
            "contacted",
        )

        invalid = self.client.patch(
            "/api/creators/xhs/u1/cooperation",
            json={"status": "adopted"},
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)

        cleared = self.client.patch(
            "/api/creators/xhs/u1/cooperation",
            json={"status": ""},
        )
        self.assertEqual(cleared.status_code, 409, cleared.text)
        self.assertEqual(
            self.client.get("/api/creators?platform=xhs").json()["items"][0][
                "cooperation_status"
            ],
            "contacted",
        )
        cancelled = self.client.patch(
            "/api/creators/xhs/u1/cooperation",
            json={"status": "cancelled"},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["cooperation_status"], "cancelled")
        self.assertEqual(self.client.get("/api/dashboard").json()["metrics"]["adopted"], 1)

    def test_cooperation_case_lifecycle_attachments_works_and_rounds(self) -> None:
        adopted = self.client.patch(
            f"/api/rounds/{self.round_id}/creators/xhs/u1",
            json={"status": "adopted"},
        )
        self.assertEqual(adopted.status_code, 200, adopted.text)
        detail = self.client.get(
            f"/api/rounds/{self.round_id}/creators/xhs/u1"
        ).json()
        self.assertEqual(detail["cooperation_count"], 1)
        self.assertEqual(len(detail["cooperation_cases"]), 1)
        case_id = detail["cooperation_cases"][0]["id"]

        status = self.client.post(
            f"/api/cooperation-cases/{case_id}/status",
            json={"status": "contacted", "note": "已加微信"},
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["case"]["status"], "contacted")

        note = self.client.post(
            f"/api/cooperation-cases/{case_id}/notes",
            json={"note": "对方希望先看报价"},
        )
        self.assertEqual(note.status_code, 200, note.text)

        summary = self.client.patch(
            f"/api/cooperation-cases/{case_id}",
            json={"summary": "图文合作，报价待确认"},
        )
        self.assertEqual(summary.status_code, 200, summary.text)

        upload = self.client.post(
            f"/api/cooperation-cases/{case_id}/attachments",
            data={"category": "quote", "description": "报价截图"},
            files={"file": ("quote.txt", b"quote evidence", "text/plain")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        attachment = upload.json()["attachments"][0]
        downloaded = self.client.get(attachment["download_url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"quote evidence")

        work = self.client.post(
            f"/api/cooperation-cases/{case_id}/works",
            json={
                "platform": "xhs",
                "url": "https://example.com/work/1",
                "title": "合作笔记",
            },
        )
        self.assertEqual(work.status_code, 200, work.text)
        work_id = work.json()["works"][0]["id"]
        updated_work = self.client.patch(
            f"/api/cooperation-works/{work_id}",
            json={
                "platform": "xhs",
                "url": "https://example.com/work/1",
                "title": "合作笔记（已更新）",
            },
        )
        self.assertEqual(updated_work.status_code, 200, updated_work.text)
        self.assertEqual(
            updated_work.json()["works"][0]["title"], "合作笔记（已更新）"
        )

        before_complete = self.client.get(
            f"/api/cooperation-cases/{case_id}"
        ).json()
        completed = self.client.post(
            f"/api/cooperation-cases/{case_id}/complete",
            json={
                "note": "已发布并结算",
                "expected_updated_at": before_complete["case"]["updated_at"],
                "works": [
                    {
                        "platform": "xhs",
                        "url": "https://example.com/work/2",
                        "title": "结案作品",
                    }
                ],
            },
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["case"]["status"], "completed")
        self.assertEqual(completed.json()["summary"]["cooperation_count"], 1)
        self.assertTrue(completed.json()["summary"]["cooperation_last_completed_at"])
        evidence = self.client.get(f"/api/cooperation-cases/{case_id}/export")
        self.assertEqual(evidence.status_code, 200, evidence.text)
        self.assertTrue(evidence.content.startswith(b"PK"))

        second = self.client.post(
            "/api/cooperation-cases",
            json={"platform": "xhs", "user_id": "u1"},
        )
        self.assertEqual(second.status_code, 200, second.text)
        second_id = second.json()["case"]["id"]
        self.assertEqual(second.json()["case"]["seq"], 2)
        self.assertEqual(second.json()["case"]["status"], "contacted")
        conflict = self.client.post(
            "/api/cooperation-cases",
            json={"platform": "xhs", "user_id": "u1"},
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(
            self.client.get(f"/api/cooperation-cases/{second_id}").status_code,
            200,
        )

    def test_cooperation_status_requires_adopted_creator(self) -> None:
        rejected = self.client.patch(
            "/api/creators/xhs/u1/cooperation",
            json={"status": "contacted"},
        )
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("只有已采用", rejected.json()["detail"])
        with Database(self.settings.output_dir) as db:
            self.assertFalse(db.get_creator("xhs", "u1")["cooperation_status"])

        adopted = self.client.patch(
            f"/api/rounds/{self.round_id}/creators/xhs/u1",
            json={"status": "adopted"},
        )
        self.assertEqual(adopted.status_code, 200, adopted.text)
        self.assertEqual(adopted.json()["cooperation_status"], "not_contacted")
        updated = self.client.patch(
            "/api/creators/xhs/u1/cooperation",
            json={"status": "contacted"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

    def test_cooperation_history_and_stale_filter(self) -> None:
        self.client.patch(
            f"/api/rounds/{self.round_id}/creators/xhs/u1",
            json={"status": "adopted"},
        )
        updated = self.client.patch(
            "/api/creators/xhs/u1/cooperation",
            json={"status": "contacted"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        detail = self.client.get(
            f"/api/rounds/{self.round_id}/creators/xhs/u1"
        ).json()
        history = detail["cooperation_history"]
        self.assertTrue(history)
        self.assertEqual(history[0]["to_status"], "contacted")
        self.assertEqual(history[0]["actor_username"], "test-admin")
        self.assertTrue(any(item["auto"] for item in history))

        with Database(self.settings.output_dir) as db:
            db._conn.execute(
                "UPDATE creators SET cooperation_updated_at = ? "
                "WHERE platform = 'xhs' AND user_id = 'u1'",
                ("2000-01-01T00:00:00.000+00:00",),
            )
            db._conn.commit()

        stale = self.client.get(
            "/api/creators?platform=xhs&cooperation=contacted"
            "&cooperation_stale_days=7"
        ).json()
        self.assertEqual(stale["total"], 1)
        fresh = self.client.get(
            "/api/creators?platform=xhs&cooperation=not_contacted"
            "&cooperation_stale_days=7"
        ).json()
        self.assertEqual(fresh["total"], 0)

    def test_create_round_without_start(self) -> None:
        response = self.client.post(
            "/api/rounds",
            json={
                "keywords": ["测试关键词"],
                "category": "测试垂类",
                "pages": 1,
                "max_creators": 1,
                "start": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["job_id"])
        self.assertIn("测试关键词", payload["round"]["keywords"])
        self.assertEqual(payload["round"]["category"], "测试垂类")

    def test_run_without_credentials_is_rejected(self) -> None:
        response = self.client.post(
            f"/api/rounds/{self.round_id}/run",
            json={"refresh_users": False, "force": False},
        )
        self.assertEqual(response.status_code, 400)

    def test_settings_and_balance_without_credentials(self) -> None:
        settings_payload = self.client.get("/api/settings").json()
        self.assertFalse(settings_payload["credentials_ok"])
        balance = self.client.get("/api/balance").json()
        self.assertIsNone(balance["remaining"])

    def test_settings_lists_platform_capabilities(self) -> None:
        settings_payload = self.client.get("/api/settings").json()
        self.assertIn("providers", settings_payload)
        self.assertIn("platforms", settings_payload)
        ids = {item["id"] for item in settings_payload["providers"]}
        self.assertEqual(ids, {"dataflow", "rnote", "everyinfra", "tikhub", "justoneapi", "socialdatax", "redfox"})
        xhs = next(item for item in settings_payload["platforms"] if item["code"] == "xhs")
        self.assertEqual({item["id"] for item in xhs["providers"]}, {"dataflow", "rnote", "everyinfra", "tikhub", "justoneapi", "socialdatax", "redfox"})
        rnote = next(item for item in xhs["providers"] if item["id"] == "rnote")
        self.assertEqual(rnote["search_cost_tokens"], 72)
        self.assertEqual(rnote["search_currency"], "USD")

    def test_update_rnote_token(self) -> None:
        env_path = Path(self.tmp.name) / ".env"
        old_env_path = api_server.ENV_PATH
        old_settings = api_server.settings
        old_key = os.environ.get("RNOTE_API_KEY")
        api_server.ENV_PATH = env_path
        api_server.settings = replace(self.settings, rnote_api_key="")
        try:
            with patch.object(
                api_server,
                "_fetch_balance",
                return_value={"remaining": 1000, "text": "ok"},
            ) as fetch_balance:
                response = self.client.put(
                    "/api/settings/rnote-token",
                    json={"token": "  rnote-key  ", "verify": True},
                )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["rnote_token_configured"])
            fetch_balance.assert_called_once()
            self.assertIn("RNOTE_API_KEY", env_path.read_text(encoding="utf-8"))
            self.assertEqual(os.environ.get("RNOTE_API_KEY"), "rnote-key")

            invalid = self.client.put(
                "/api/settings/rnote-token",
                json={"token": "line1\nline2"},
            )
            self.assertEqual(invalid.status_code, 400)
        finally:
            api_server.ENV_PATH = old_env_path
            api_server.settings = old_settings
            if old_key is None:
                os.environ.pop("RNOTE_API_KEY", None)
            else:
                os.environ["RNOTE_API_KEY"] = old_key

    def test_update_extra_provider_token(self) -> None:
        env_path = Path(self.tmp.name) / ".env"
        old_env_path = api_server.ENV_PATH
        old_settings = api_server.settings
        old_key = os.environ.get("EVERYINFRA_API_KEY")
        api_server.ENV_PATH = env_path
        api_server.settings = replace(self.settings)
        try:
            response = self.client.put(
                "/api/settings/providers/everyinfra/token",
                json={"token": "  every-key  ", "verify": False},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("EVERYINFRA_API_KEY", env_path.read_text(encoding="utf-8"))
            self.assertEqual(os.environ.get("EVERYINFRA_API_KEY"), "every-key")
            providers = {item["id"] for item in response.json()["providers"]}
            self.assertIn("everyinfra", providers)
        finally:
            api_server.ENV_PATH = old_env_path
            api_server.settings = old_settings
            if old_key is None:
                os.environ.pop("EVERYINFRA_API_KEY", None)
            else:
                os.environ["EVERYINFRA_API_KEY"] = old_key

    def test_create_round_for_rnote_provider(self) -> None:
        old_settings = api_server.settings
        api_server.settings = replace(
            self.settings,
            rnote_api_key="test-rnote-key",
            rnote_host="https://rnote.dev",
            rnote_allowed_hosts=("rnote.dev",),
        )
        try:
            response = self.client.post(
                "/api/rounds",
                json={
                    "keywords": ["Rnote测试"],
                    "category": "测试",
                    "pages": 1,
                    "max_creators": 1,
                    "platform": "xhs",
                    "provider": "rnote",
                    "start": False,
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()["round"]
            self.assertEqual(payload["provider"], "rnote")
            self.assertEqual(payload["provider_label"], "Rnote")
        finally:
            api_server.settings = old_settings

    def test_update_dataflow_token(self) -> None:
        env_path = Path(self.tmp.name) / ".env"
        old_env_path = api_server.ENV_PATH
        old_settings = api_server.settings
        old_token = os.environ.get("DATAFLOW_TOKEN")
        api_server.ENV_PATH = env_path
        api_server.settings = replace(
            self.settings,
            host="https://dataflow.example",
            token="",
            dataflow_allowed_hosts=("dataflow.example",),
        )
        try:
            response = self.client.put(
                "/api/settings/dataflow-token",
                json={"token": "  new-token-value  ", "verify": False},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["token_configured"])
            self.assertTrue(payload["credentials_ok"])
            self.assertNotIn("new-token-value", json.dumps(payload))
            self.assertIn("DATAFLOW_TOKEN", env_path.read_text(encoding="utf-8"))
            self.assertEqual(os.environ.get("DATAFLOW_TOKEN"), "new-token-value")

            with patch.object(
                api_server,
                "_fetch_balance",
                return_value={"remaining": 321, "text": "剩余 321"},
            ) as fetch_balance:
                verified = self.client.put(
                    "/api/settings/dataflow-token",
                    json={"token": "verified-token", "verify": True},
                )
            self.assertEqual(verified.status_code, 200)
            self.assertEqual(verified.json()["balance"]["remaining"], 321)
            fetch_balance.assert_called_once()
            self.assertIn("verified-token", env_path.read_text(encoding="utf-8"))

            with patch.object(
                api_server,
                "_fetch_balance",
                side_effect=RuntimeError("invalid token"),
            ):
                rejected = self.client.put(
                    "/api/settings/dataflow-token",
                    json={"token": "rejected-token", "verify": True},
                )
            self.assertEqual(rejected.status_code, 400)
            self.assertIn("令牌验证失败", rejected.json()["detail"])
            self.assertNotIn("rejected-token", env_path.read_text(encoding="utf-8"))
            self.assertEqual(os.environ.get("DATAFLOW_TOKEN"), "verified-token")
            self.assertTrue(api_server.settings.token == "verified-token")

            empty = self.client.put(
                "/api/settings/dataflow-token", json={"token": "   ", "verify": False}
            )
            self.assertEqual(empty.status_code, 400)
            invalid = self.client.put(
                "/api/settings/dataflow-token",
                json={"token": "line1\nline2", "verify": False},
            )
            self.assertEqual(invalid.status_code, 400)
        finally:
            api_server.ENV_PATH = old_env_path
            api_server.settings = old_settings
            if old_token is None:
                os.environ.pop("DATAFLOW_TOKEN", None)
            else:
                os.environ["DATAFLOW_TOKEN"] = old_token

    def test_round_archive_and_safe_purge(self) -> None:
        impact = self.client.get(f"/api/rounds/{self.round_id}/impact")
        self.assertEqual(impact.status_code, 200, impact.text)
        self.assertTrue(impact.json()["can_purge"])
        archived = self.client.post(f"/api/rounds/{self.round_id}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertTrue(archived.json()["archived"])
        active = self.client.get("/api/rounds").json()
        self.assertFalse(any(item["id"] == self.round_id for item in active))
        all_rounds = self.client.get("/api/rounds?include_archived=true").json()
        self.assertTrue(any(item["id"] == self.round_id for item in all_rounds))
        restored = self.client.post(f"/api/rounds/{self.round_id}/restore")
        self.assertEqual(restored.status_code, 200)
        self.assertFalse(restored.json()["archived"])

        wrong_name = self.client.request(
            "DELETE",
            f"/api/rounds/{self.round_id}",
            json={"confirm_name": "wrong"},
        )
        self.assertEqual(wrong_name.status_code, 400)
        deleted = self.client.request(
            "DELETE",
            f"/api/rounds/{self.round_id}",
            json={"confirm_name": "API-TEST"},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertIn("backup", deleted.json())
        self.assertEqual(self.client.get(f"/api/rounds/{self.round_id}").status_code, 404)

    def test_round_with_leads_cannot_be_purged(self) -> None:
        lead = self.client.post(
            "/api/leads",
            json={
                "platform": "xhs",
                "nickname": "保留线索",
                "source_round_id": self.round_id,
            },
        )
        self.assertEqual(lead.status_code, 200, lead.text)
        impact = self.client.get(f"/api/rounds/{self.round_id}/impact").json()
        self.assertFalse(impact["can_purge"])
        response = self.client.request(
            "DELETE",
            f"/api/rounds/{self.round_id}",
            json={"confirm_name": "API-TEST"},
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_owner_and_expected_status_are_rejected(self) -> None:
        self.assertEqual(self.client.get("/api/creators?owner=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/leads?owner=abc").status_code, 400)
        response = self.client.patch(
            f"/api/rounds/{self.round_id}/creators/xhs/u1",
            json={"status": "shortlisted", "expected_status": "bogus"},
        )
        self.assertEqual(response.status_code, 400)

    def test_member_without_collection_cannot_mutate_keywords(self) -> None:
        with Database(self.settings.output_dir) as db:
            db.create_user(
                "viewer-test",
                hash_password("viewer-password-123"),
                role="member",
                can_collect=False,
            )
        login = self.client.post(
            "/api/auth/login",
            json={"username": "viewer-test", "password": "viewer-password-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
        response = self.client.post("/api/keywords", json={"word": "越权关键词"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
