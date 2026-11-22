from __future__ import annotations

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
            json={"username": "new-user", "password": "new-password-123", "role": "member"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 403)
        self.assertEqual(
            self.client.post("/api/auth/logout", headers=headers).status_code,
            200,
        )


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
