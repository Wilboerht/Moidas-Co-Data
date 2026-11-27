from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.db import Database


class EmailAccountDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name))
        self.round_id = self.db.create_round(
            ["咖啡"], pages=1, max_creators=None, name="EMAIL-DB"
        )
        self.db.upsert_creator_basic(
            {"platform": "xhs", "user_id": "u-email", "nickname": "达人"}
        )
        self.db.add_creator_round(self.round_id, "xhs", "u-email")
        self.db.update_review(self.round_id, "xhs", "u-email", status="adopted")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _account(self, *, scope: str = "personal", owner: int | None = 1) -> int:
        return self.db.create_email_account(
            scope=scope,
            owner_user_id=owner,
            label="测试",
            from_name="运营",
            from_email="ops@example.com",
            reply_to="",
            host="smtp.example.com",
            port=587,
            security="starttls",
            username="ops@example.com",
            password_encrypted="token",
            key_fingerprint="fp",
            daily_limit=20,
            created_by=1,
        )

    def _campaign(
        self,
        *,
        account_id: int,
        recipients: list[dict[str, str]] | None = None,
        schedule: bool = False,
    ) -> dict:
        return self.db.create_email_campaign(
            name="外联",
            account_id=account_id,
            subject="Hi",
            body="Hello",
            timezone="UTC",
            window_start="00:00",
            window_end="23:59",
            weekdays="[1,2,3,4,5,6,7]",
            interval_seconds=60,
            jitter_seconds=0,
            daily_limit=20,
            auto_mark_contacted=False,
            recipients=recipients
            or [
                {
                    "platform": "xhs",
                    "user_id": "u-email",
                    "email": "cool@example.com",
                }
            ],
            created_by=1,
            scheduled_start="2030-01-01T00:00:00+00:00" if schedule else None,
            schedule=schedule,
        )

    def test_account_scope_and_updates(self) -> None:
        personal = self._account(owner=7)
        shared = self._account(scope="shared", owner=None)
        self.assertEqual(
            [item["id"] for item in self.db.list_email_accounts(7)],
            [shared, personal],
        )
        self.assertEqual(
            [item["id"] for item in self.db.list_email_accounts(8)], [shared]
        )
        self.db.update_email_account(personal, label="新名称", daily_limit=50)
        row = self.db.get_email_account(personal)
        self.assertEqual(row["label"], "新名称")
        self.assertEqual(row["daily_limit"], 50)
        self.db.mark_email_account_test(personal, "认证失败")
        self.assertEqual(self.db.get_email_account(personal)["last_test_error"], "认证失败")
        self.db.delete_email_account(personal)
        self.assertIsNone(self.db.get_email_account(personal))

    def test_creator_emails_primary_switch_and_dedupe(self) -> None:
        first = self.db.add_creator_email(
            "xhs", "u-email", "A@Example.com", label="主邮箱", is_primary=True
        )
        self.assertEqual(first["email_normalized"], "a@example.com")
        self.assertEqual(first["is_primary"], 1)
        second = self.db.add_creator_email(
            "xhs", "u-email", "b@example.com", is_primary=False
        )
        self.assertEqual(second["is_primary"], 0)
        promoted = self.db.set_primary_creator_email(second["id"])
        self.assertEqual(promoted["is_primary"], 1)
        items = self.db.creator_emails("xhs", "u-email")
        self.assertEqual([item["email_normalized"] for item in items], ["b@example.com", "a@example.com"])
        self.assertEqual([item["is_primary"] for item in items], [1, 0])
        updated = self.db.add_creator_email(
            "xhs", "u-email", "a@example.com", label="更新", is_primary=False
        )
        self.assertEqual(updated["label"], "更新")
        with self.assertRaises(ValueError):
            self.db.add_creator_email("xhs", "u-email", "bad-email")

    def test_target_cooldown_is_reported(self) -> None:
        self.db.update_review(
            self.round_id, "xhs", "u-email", status="adopted"
        )
        self.db.add_creator_email(
            "xhs", "u-email", "cool@example.com", is_primary=True
        )
        account_id = self._account()
        created = self.db.create_email_campaign(
            name="冷却",
            account_id=account_id,
            subject="Hi",
            body="Hi",
            timezone="UTC",
            window_start="00:00",
            window_end="23:59",
            weekdays="[1,2,3,4,5,6,7]",
            interval_seconds=60,
            jitter_seconds=0,
            daily_limit=20,
            auto_mark_contacted=False,
            recipients=[
                {
                    "platform": "xhs",
                    "user_id": "u-email",
                    "email": "cool@example.com",
                }
            ],
            created_by=1,
        )
        stamp = datetime.now(timezone.utc).isoformat()
        self.db._conn.execute(
            "UPDATE email_recipients SET status = 'sent', sent_at = ?, "
            "sent_date = ? WHERE campaign_id = ?",
            (stamp, datetime.now(timezone.utc).date().isoformat(), created["campaign_id"]),
        )
        self.db._conn.commit()
        result = self.db.email_targets(
            platform="xhs", cooldown_days=30, creator_cooldown_days=90
        )
        self.assertEqual(result["total"], 1)
        item = result["items"][0]
        self.assertTrue(item["cooldown_until"])
        self.assertEqual(item["cooldown_reason"], "creator")
        self.assertEqual(item["last_email_sent_at"], stamp)

    def test_user_permission_column(self) -> None:
        user_id = self.db.create_user("sender", "hash", can_send_email=True)
        self.assertTrue(self.db.get_user(user_id)["can_send_email"])
        self.db.update_user(user_id, can_send_email=False)
        self.assertFalse(self.db.get_user(user_id)["can_send_email"])
    def test_connection_update_invalidates_test_and_auth_state(self) -> None:
        account_id = self._account()
        self.db.mark_email_account_test(account_id, None)
        self.db.mark_email_account_auth_failed(account_id, "旧认证错误")
        self.db.update_email_account(account_id, host="smtp.new.example.com")
        account = self.db.get_email_account(account_id)
        self.assertIsNone(account["last_test_at"])
        self.assertEqual(account["last_test_error"], "")
        self.assertIsNone(account["auth_failed_at"])
        self.assertEqual(account["auth_error"], "")

    def test_deleting_primary_email_promotes_next(self) -> None:
        first = self.db.add_creator_email(
            "xhs", "u-email", "first@example.com", is_primary=True
        )
        second = self.db.add_creator_email(
            "xhs", "u-email", "second@example.com"
        )
        self.db.delete_creator_email(first["id"])
        emails = self.db.creator_emails("xhs", "u-email")
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["id"], second["id"])
        self.assertEqual(emails[0]["is_primary"], 1)

    def test_atomic_schedule_duplicate_protection_and_account_delete_guard(self) -> None:
        account_id = self._account()
        first = self._campaign(account_id=account_id, schedule=True)
        self.assertEqual(
            self.db.email_campaign_detail(first["campaign_id"])["status"],
            "scheduled",
        )
        with self.assertRaises(ValueError):
            self.db.delete_email_account(account_id)
        with self.assertRaises(ValueError):
            self._campaign(account_id=account_id)
        self.db.upsert_creator_basic(
            {"platform": "xhs", "user_id": "u-two", "nickname": "达人二"}
        )
        self.db.add_creator_round(self.round_id, "xhs", "u-two")
        self.db.update_review(self.round_id, "xhs", "u-two", status="adopted")
        mixed = self._campaign(
            account_id=account_id,
            recipients=[
                {
                    "platform": "xhs",
                    "user_id": "u-email",
                    "email": "cool@example.com",
                },
                {
                    "platform": "xhs",
                    "user_id": "u-two",
                    "email": "two@example.com",
                },
            ],
        )
        self.assertEqual(mixed["inserted"], 1)
        self.assertEqual(len(mixed["skipped"]), 1)
        self.assertIn("未结束", mixed["skipped"][0]["reason"])

    def test_zero_valid_recipients_rolls_back_campaign_and_email(self) -> None:
        account_id = self._account()
        self.db.add_email_suppression("cool@example.com", reason="退订")
        with self.assertRaises(ValueError):
            self._campaign(account_id=account_id)
        campaigns = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM email_campaigns"
        ).fetchone()["n"]
        self.assertEqual(campaigns, 0)
        self.assertEqual(self.db.creator_emails("xhs", "u-email"), [])

    def test_disabled_account_cannot_be_scheduled(self) -> None:
        account_id = self._account()
        campaign = self._campaign(account_id=account_id)
        self.db.update_email_account(account_id, enabled=False)
        with self.assertRaises(ValueError):
            self.db.schedule_email_campaign(
                campaign["campaign_id"],
                scheduled_start=None,
                timezone="UTC",
                window_start="00:00",
                window_end="23:59",
                weekdays="[1,2,3,4,5,6,7]",
                interval_seconds=60,
                jitter_seconds=0,
                daily_limit=20,
            )
        self.assertEqual(
            self.db.email_campaign_detail(campaign["campaign_id"])["status"],
            "draft",
        )


if __name__ == "__main__":
    unittest.main()
