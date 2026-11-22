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
            "source",
            "platform_label",
            "owner_user_id",
            "owner_username",
            "claimed_at",
            "cooperation_platform",
            "raw_title",
            "remark",
            "sample_notes",
            "sample_collected",
            "sample_comments",
        ):
            self.assertIn(field, creator)

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
        self.assertIn("经手人", text)
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

    def test_lead_owner_authorization(self) -> None:
        created_user = self.client.post(
            "/api/users",
            json={
                "username": "lead-member",
                "password": "lead-password-123",
                "role": "member",
                "can_collect": True,
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

    def test_active_job_endpoint(self) -> None:
        with Database(self.settings.output_dir) as db:
            job_id = db.claim_job(self.round_id)
        response = self.client.get("/api/jobs/active")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], job_id)
        self.assertEqual(response.json()["round_id"], self.round_id)
        cancelled = self.client.post(f"/api/jobs/{job_id}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "failed")
        self.assertIsNone(self.client.get("/api/jobs/active").json())

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
        self.assertIn("tiers", facets)

        meta = self.client.get("/api/meta").json()
        codes = {item["code"] for item in meta["statuses"]}
        self.assertIn("pending", codes)
        self.assertIn("adopted", codes)
        self.assertNotIn("recommended", codes)

    def test_fts_search(self) -> None:
        found = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&search=咖啡师"
        ).json()
        self.assertEqual(found["total"], 1)
        missing = self.client.get(
            f"/api/rounds/{self.round_id}/creators?platform=xhs&search=不存在的人"
        ).json()
        self.assertEqual(missing["total"], 0)

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

        all_dashboard = self.client.get("/api/dashboard").json()
        self.assertIsNone(all_dashboard["round"])
        self.assertEqual(all_dashboard["metrics"]["creators"], 1)
        self.assertEqual(len(all_dashboard["top_creators"]), 1)

        csv_response = self.client.get(f"/api/rounds/{self.round_id}/export?format=csv")
        self.assertEqual(csv_response.status_code, 200)
        text = csv_response.content.decode("utf-8-sig")
        self.assertIn("昵称", text)
        self.assertIn("主页URL", text)
        self.assertIn("账号ID", text)

        xlsx_response = self.client.get(f"/api/rounds/{self.round_id}/export?format=xlsx")
        self.assertEqual(xlsx_response.status_code, 200)
        self.assertGreater(len(xlsx_response.content), 100)

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


if __name__ == "__main__":
    unittest.main()
