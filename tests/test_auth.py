from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from codata import api_server
from codata.auth import hash_password
from codata.config import Settings
from codata.db import Database
from codata.normalize import utc_now


class AuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
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
            db.create_user("admin-test", hash_password("admin-password-123"), role="admin")
            db.create_user("member-test", hash_password("member-password-123"), role="member")

    def tearDown(self) -> None:
        api_server.app.dependency_overrides.clear()
        api_server.settings = self.old_settings
        self.tmp.cleanup()

    def login(self, username: str, password: str) -> dict:
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_unauthenticated_api_is_blocked(self) -> None:
        response = self.client.get("/api/rounds")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me").json(), {"user": None, "csrf_token": ""})

    def test_wrong_password_is_rejected(self) -> None:
        response = self.client.post(
            "/api/auth/login", json={"username": "admin-test", "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 401)

    def test_member_cannot_manage_users_or_token(self) -> None:
        login = self.login("member-test", "member-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        self.assertEqual(self.client.get("/api/users").status_code, 403)
        response = self.client.put(
            "/api/settings/dataflow-token",
            json={"token": "x", "verify": False},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_manage_users_and_csrf_is_required(self) -> None:
        login = self.login("admin-test", "admin-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        users = self.client.get("/api/users")
        self.assertEqual(users.status_code, 200)
        self.assertEqual(len(users.json()), 2)
        self.assertEqual(
            self.client.post("/api/users", json={"username": "x", "password": "1234567890"}).status_code,
            403,
        )
        response = self.client.post(
            "/api/users",
            json={"username": "new-user", "password": "new-password-123word-123", "role": "member"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 403)
        self.assertEqual(
            self.client.post("/api/auth/logout", headers=headers).status_code,
            200,
        )

    def test_weak_password_is_rejected(self) -> None:
        login = self.login("admin-test", "admin-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        response = self.client.post(
            "/api/users",
            json={"username": "weak-user", "password": "12345", "role": "member"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_eight_character_password_is_allowed(self) -> None:
        login = self.login("admin-test", "admin-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        response = self.client.post(
            "/api/users",
            json={"username": "short-pass", "password": "12345678", "role": "member"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["must_change_password"])

    def test_password_change_rejects_current_password(self) -> None:
        login = self.login("member-test", "member-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        response = self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": "member-password-123",
                "new_password": "member-password-123",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("不能与当前密码相同", response.json()["detail"])
        self.login("member-test", "member-password-123")

    def test_password_change_keeps_current_session_and_updates_password(self) -> None:
        login = self.login("member-test", "member-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        response = self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": "member-password-123",
                "new_password": "member-password-456",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/rounds").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"username": "member-test", "password": "member-password-123"},
            ).status_code,
            401,
        )
        self.login("member-test", "member-password-456")


    def test_member_collection_permission_and_forced_password(self) -> None:
        admin = self.login("admin-test", "admin-password-123")
        admin_headers = {"X-CSRF-Token": admin["csrf_token"]}
        created = self.client.post(
            "/api/users",
            json={
                "username": "limited-test",
                "password": "limited-password-123",
                "role": "member",
                "can_collect": False,
            },
            headers=admin_headers,
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["must_change_password"])
        self.assertFalse(created.json()["can_collect"])
        limited_id = created.json()["id"]
        self.client.post("/api/auth/logout", headers=admin_headers)

        limited = self.login("limited-test", "limited-password-123")
        limited_headers = {"X-CSRF-Token": limited["csrf_token"]}
        self.assertTrue(limited["user"]["must_change_password"])
        self.assertEqual(self.client.get("/api/rounds").status_code, 403)
        changed = self.client.post(
            "/api/auth/change-password",
            json={"current_password": "limited-password-123", "new_password": "limited-password-456"},
            headers=limited_headers,
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.client.get("/api/rounds").status_code, 200)
        denied = self.client.post(
            "/api/rounds",
            json={"keywords": ["测试"], "pages": 1, "max_creators": 1, "start": False},
            headers=limited_headers,
        )
        self.assertEqual(denied.status_code, 403)
        self.client.post("/api/auth/logout", headers=limited_headers)

        admin = self.login("admin-test", "admin-password-123")
        admin_headers = {"X-CSRF-Token": admin["csrf_token"]}
        updated = self.client.patch(
            f"/api/users/{limited_id}",
            json={"can_collect": True},
            headers=admin_headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertTrue(updated.json()["can_collect"])
        self.client.post("/api/auth/logout", headers=admin_headers)

        limited = self.login("limited-test", "limited-password-456")
        limited_headers = {"X-CSRF-Token": limited["csrf_token"]}
        allowed = self.client.post(
            "/api/rounds",
            json={"keywords": ["测试"], "pages": 1, "max_creators": 1, "start": False},
            headers=limited_headers,
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_lead_editor_permission_without_collect(self) -> None:
        admin = self.login("admin-test", "admin-password-123")
        admin_headers = {"X-CSRF-Token": admin["csrf_token"]}
        created = self.client.post(
            "/api/users",
            json={
                "username": "lead-editor",
                "password": "lead-editor-password-123",
                "role": "member",
                "can_collect": False,
                "can_edit_leads": True,
            },
            headers=admin_headers,
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertFalse(created.json()["can_collect"])
        self.assertTrue(created.json()["can_edit_leads"])
        editor_id = created.json()["id"]
        self.client.post("/api/auth/logout", headers=admin_headers)

        login = self.login("lead-editor", "lead-editor-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        changed = self.client.post(
            "/api/auth/change-password",
            json={"current_password": "lead-editor-password-123", "new_password": "lead-editor-password-456"},
            headers=headers,
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        denied_round = self.client.post(
            "/api/rounds",
            json={"keywords": ["测试"], "pages": 1, "max_creators": 1, "start": False},
            headers=headers,
        )
        self.assertEqual(denied_round.status_code, 403)
        created_lead = self.client.post(
            "/api/leads",
            json={"platform": "xhs", "nickname": "线索编辑者提供的线索"},
            headers=headers,
        )
        self.assertEqual(created_lead.status_code, 200, created_lead.text)
        lead_id = created_lead.json()["id"]
        updated = self.client.patch(
            f"/api/leads/{lead_id}",
            json={"nickname": "更新后的线索"},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        assigned = self.client.post(
            f"/api/leads/{lead_id}/assign",
            json={"owner_user_id": editor_id},
            headers=headers,
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        matched = self.client.post(f"/api/leads/{lead_id}/match", headers=headers)
        self.assertEqual(matched.status_code, 200, matched.text)
        bulk = self.client.post(
            "/api/leads/bulk",
            json={"items": [{"platform": "xhs", "nickname": "批量线索"}]},
            headers=headers,
        )
        self.assertEqual(bulk.status_code, 200, bulk.text)
        lead_export = self.client.get("/api/leads/export?format=csv")
        self.assertEqual(lead_export.status_code, 200)
        lead_header = lead_export.content.decode("utf-8-sig").splitlines()[0]
        self.assertNotIn("来源轮次", lead_header)
        self.assertNotIn("匹配轮次", lead_header)
        creators_export = self.client.get("/api/creators/export?format=csv")
        self.assertEqual(creators_export.status_code, 200)
        creators_header = creators_export.content.decode("utf-8-sig").splitlines()[0]
        self.assertNotIn("数据轮次", creators_header)
        denied_convert = self.client.post(
            f"/api/leads/{lead_id}/convert",
            json={},
            headers=headers,
        )
        self.assertEqual(denied_convert.status_code, 403)
        self.client.post("/api/auth/logout", headers=headers)

        admin = self.login("admin-test", "admin-password-123")
        admin_headers = {"X-CSRF-Token": admin["csrf_token"]}
        revoked = self.client.patch(
            f"/api/users/{editor_id}",
            json={"can_edit_leads": False},
            headers=admin_headers,
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertFalse(revoked.json()["can_edit_leads"])
        self.client.post("/api/auth/logout", headers=admin_headers)

        login = self.login("lead-editor", "lead-editor-password-456")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        denied_lead = self.client.post(
            "/api/leads",
            json={"platform": "xhs", "nickname": "无权限线索"},
            headers=headers,
        )
        self.assertEqual(denied_lead.status_code, 403)

    def test_self_service_restrictions(self) -> None:
        login = self.login("admin-test", "admin-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        users = self.client.get("/api/users").json()
        admin_id = next(user["id"] for user in users if user["username"] == "admin-test")
        reset = self.client.post(
            f"/api/users/{admin_id}/reset-password",
            json={"new_password": "123456789012"},
            headers=headers,
        )
        self.assertEqual(reset.status_code, 400, reset.text)
        created = self.client.post(
            "/api/users",
            json={
                "username": "admin-two",
                "password": "admin-two-password",
                "role": "admin",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 200, created.text)
        demote = self.client.patch(
            f"/api/users/{admin_id}", json={"role": "member"}, headers=headers
        )
        self.assertEqual(demote.status_code, 400, demote.text)
        self.assertIn("不能修改自己的角色", demote.json()["detail"])

    def test_revoke_own_sessions_keeps_current(self) -> None:
        login = self.login("admin-test", "admin-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        users = self.client.get("/api/users").json()
        admin_id = next(user["id"] for user in users if user["username"] == "admin-test")
        other = TestClient(api_server.app, base_url="http://127.0.0.1")
        other.post(
            "/api/auth/login",
            json={"username": "admin-test", "password": "admin-password-123"},
        )
        response = self.client.post(
            f"/api/users/{admin_id}/revoke-sessions", headers=headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["kept_current"])
        self.assertGreaterEqual(payload["revoked"], 1)
        self.assertEqual(self.client.get("/api/rounds").status_code, 200)
        self.assertEqual(other.get("/api/rounds").status_code, 401)

    def test_can_collect_change_applies_without_relogin_and_is_audited(self) -> None:
        admin = self.login("admin-test", "admin-password-123")
        admin_headers = {"X-CSRF-Token": admin["csrf_token"]}
        users = self.client.get("/api/users").json()
        member_id = next(user["id"] for user in users if user["username"] == "member-test")
        member_client = TestClient(api_server.app, base_url="http://127.0.0.1")
        member = member_client.post(
            "/api/auth/login",
            json={"username": "member-test", "password": "member-password-123"},
        ).json()
        member_headers = {"X-CSRF-Token": member["csrf_token"]}
        denied = member_client.post(
            "/api/rounds",
            json={"keywords": ["测试"], "pages": 1, "max_creators": 1, "start": False},
            headers=member_headers,
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        updated = self.client.patch(
            f"/api/users/{member_id}", json={"can_collect": True}, headers=admin_headers
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        allowed = member_client.post(
            "/api/rounds",
            json={"keywords": ["测试"], "pages": 1, "max_creators": 1, "start": False},
            headers=member_headers,
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        with Database(self.settings.output_dir) as db:
            row = db._conn.execute(
                "SELECT details FROM audit_log WHERE action = 'user.updated' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        details = json.loads(row["details"])
        self.assertEqual(
            details["changes"]["can_collect"], {"from": False, "to": True}
        )

    def test_reset_password_revokes_sessions_and_forces_change(self) -> None:
        admin = self.login("admin-test", "admin-password-123")
        admin_headers = {"X-CSRF-Token": admin["csrf_token"]}
        users = self.client.get("/api/users").json()
        member_id = next(user["id"] for user in users if user["username"] == "member-test")
        member_client = TestClient(api_server.app, base_url="http://127.0.0.1")
        member_client.post(
            "/api/auth/login",
            json={"username": "member-test", "password": "member-password-123"},
        )
        reset = self.client.post(
            f"/api/users/{member_id}/reset-password",
            json={"new_password": "new-password-123"},
            headers=admin_headers,
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(member_client.get("/api/rounds").status_code, 401)
        relogin = member_client.post(
            "/api/auth/login",
            json={"username": "member-test", "password": "new-password-123"},
        ).json()
        self.assertTrue(relogin["user"]["must_change_password"])
        headers = {"X-CSRF-Token": relogin["csrf_token"]}
        self.assertEqual(member_client.get("/api/rounds").status_code, 403)
        self.assertEqual(
            member_client.post(
                "/api/auth/change-password",
                json={"current_password": "new-password-123", "new_password": "new-password-123-2"},
                headers=headers,
            ).status_code,
            200,
        )

    def test_prune_maintenance_caps_sessions(self) -> None:
        now = utc_now()
        with Database(self.settings.output_dir) as db:
            user = db.get_user_by_username("member-test")
            for index in range(25):
                db._conn.execute(
                    """
                    INSERT INTO sessions
                    (token_hash, user_id, csrf_token, created_at, expires_at, last_seen)
                    VALUES (?, ?, 'csrf', ?, '2999-01-01T00:00:00+00:00', ?)
                    """,
                    (f"cap-token-{index}", user["id"], now, now),
                )
            db._conn.commit()
            result = db.prune_maintenance()
            remaining = db._conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user["id"],)
            ).fetchone()[0]
        self.assertEqual(remaining, 20)
        self.assertEqual(result["sessions_overflow"], 5)

    def test_last_admin_cannot_be_disabled_or_demoted(self) -> None:
        login = self.login("admin-test", "admin-password-123")
        headers = {"X-CSRF-Token": login["csrf_token"]}
        users = self.client.get("/api/users").json()
        admin_id = next(user["id"] for user in users if user["username"] == "admin-test")
        response = self.client.patch(
            f"/api/users/{admin_id}",
            json={"disabled": True},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(
            f"/api/users/{admin_id}",
            json={"role": "member"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
