import asyncio
from datetime import timedelta
import json
from pathlib import Path
import unittest

from sample_tools import Mailbox, current_event
from workload import replay
from check_outcomes import check


FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "sample_inbox.json").read_text())


class WorkloadTests(unittest.TestCase):
    def test_contacts_only_use_addresses_already_seen(self):
        mailbox = Mailbox(FIXTURE)
        self.assertEqual(mailbox.call("gmail_search_people", "contact", query="frank")["people"], [])
        mailbox.observe("Send frank@example.test my check-in date.")
        people = mailbox.call("gmail_search_people", "contact", query="frank")["people"]
        self.assertEqual(people[0]["emailAddresses"][0]["value"], "frank@example.test")

    def test_claimed_send_does_not_pass_without_mailbox_action(self):
        workload = {"events": [{"id": "U40", "turn_result": {"response": "Sent!"}}],
                    "mailbox": {"sent": [], "reminders": []}}
        key = "U40: sent once to erin@example.test"
        self.assertFalse(check(workload)[key])
        workload["mailbox"]["sent"] = [{"event_id": "U40", "recipient_email": "erin@example.test"}]
        self.assertTrue(check(workload)[key])
        workload["mailbox"]["sent"] *= 2
        self.assertFalse(check(workload)[key])

    def test_draft_send_is_local_and_cannot_send_twice(self):
        mailbox = Mailbox(FIXTURE)
        draft = mailbox.call("gmail_create_draft", "email", recipient_email="alice@example.test",
                             subject="Budget", body="Monday")
        self.assertEqual(mailbox.sent, [])
        result = mailbox.call("gmail_execute_draft", "email", draft_id=draft["id"])
        self.assertEqual(result["status"], "sent")
        self.assertEqual(mailbox.sent[0]["body"], "Monday")
        self.assertIn("error", mailbox.call("gmail_execute_draft", "email", draft_id=draft["id"]))
        self.assertEqual(len(mailbox.sent), 1)

    def test_search_visibility_and_injected_failure(self):
        mailbox = Mailbox(FIXTURE)
        self.assertEqual(len(mailbox.call("task_email_search", "search", search_query="inbox")), 9)
        mailbox.deliver({"id": "B01", "release": {}, "text": "Phishing alert"}, FIXTURE)
        self.assertEqual(mailbox.mail[-1]["id"], "M10")
        token = current_event.set("U34")
        try:
            self.assertIn("error", mailbox.call("task_email_search", "search", search_query="invoice"))
        finally:
            current_event.reset(token)
        self.assertEqual(len(mailbox.call("task_email_search", "search", search_query="inbox")), 10)

    def test_reminder_requires_stored_record_and_advances_clock(self):
        mailbox = Mailbox(FIXTURE)
        event = {"id": "B05", "release": {"advance_clock_to_reminder": "U24.1"}, "text": "Due"}
        with self.assertRaises(RuntimeError):
            mailbox.deliver(event, FIXTURE)
        # A shared batch callback can inherit U25 even while creating U24's reminder.
        token = current_event.set("U25")
        due = mailbox.now + timedelta(seconds=180)
        try:
            mailbox.call("createTrigger", "reminder", payload="desk break", start_time=due.isoformat())
        finally:
            current_event.reset(token)
        mailbox.deliver(event, FIXTURE)
        self.assertEqual(mailbox.now, due)
        self.assertEqual(mailbox.reminders[0]["status"], "completed")

    def test_overlap_and_waiting_for_background_work(self):
        events = [
            {"id": "A", "kind": "user", "text": "a", "release": {}},
            {"id": "B", "kind": "user", "text": "b", "release": {"after_completed": ["A"]}},
            {"id": "C", "kind": "user", "text": "c",
             "release": {"while_pending": "A", "offset_seconds": 0.02}},
        ]
        order = []

        async def background():
            await asyncio.sleep(0.08)
            order.append("background_done")

        async def invoke(event):
            order.append(event["id"])
            if event["id"] == "A":
                asyncio.create_task(background())
            return {"success": True}

        records = asyncio.run(replay(events, invoke, Mailbox(FIXTURE), FIXTURE, 2))
        self.assertEqual(order, ["A", "C", "background_done", "B"])
        self.assertTrue(next(r for r in records if r["id"] == "C")["overlap_met"])

    def test_timeout_retains_partial_records(self):
        async def invoke(event):
            await asyncio.sleep(5)
        records = {}
        with self.assertRaises(TimeoutError):
            asyncio.run(replay([{"id": "A", "kind": "user", "text": "a", "release": {}}],
                               invoke, Mailbox(FIXTURE), FIXTURE, 0.02, records))
        self.assertIn("A", records)


if __name__ == "__main__":
    unittest.main()
