import asyncio
from datetime import timedelta
import json
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile

from sample_tools import Mailbox, current_event
from workload import ROOT, BENCHMARK_MODEL, select_server_root, replay
from check_outcomes import check, evaluate


FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "sample_inbox.json").read_text())


class WorkloadTests(unittest.TestCase):
    def test_both_modes_select_current_repository(self):
        self.assertEqual(select_server_root(True), ROOT)
        self.assertEqual(select_server_root(False), ROOT)
        self.assertEqual(BENCHMARK_MODEL, "google/gemini-2.5-flash")

    def test_both_modes_really_display_drafts(self):
        for enabled in (False, True):
            with self.subTest(draft_ready=enabled), tempfile.TemporaryDirectory() as directory:
                output_dir = Path(directory)
                command = [sys.executable, str(ROOT / "benchmarks/interaction_llm_calls/runner.py"),
                           "--output-dir", str(output_dir), "--", sys.executable,
                           str(ROOT / "benchmarks/interaction_llm_calls/workload.py"),
                           "--draft-ready" if enabled else "--no-draft-ready",
                           "--smoke", "--draft-check", "--timeout", "10"]
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(next(output_dir.glob("*/report.json")).read_text())
                self.assertEqual(report["task_outcomes"], "passed")
                self.assertEqual(report["model"], BENCHMARK_MODEL)
                self.assertEqual(report["server_root"], str(select_server_root(enabled)))
                self.assertEqual(report["draft_ready"], enabled)
                if enabled:
                    self.assertEqual(report["total_calls"], 0)
                    self.assertEqual(report["total_turns"], 0)
                else:
                    self.assertEqual(report["agent_turn_calls"], 1)
                    self.assertEqual(report["agent_turns"], 1)
                    self.assertEqual(report["user_turns"], 0)

    @staticmethod
    def outcome_workload(event_id, evidence, received_at="2026-09-17T10:00:00+05:30"):
        return {
            "events": [{"id": event_id, "kind": "user", "received_at": received_at,
                        "turn_result": {"success": True}, "evidence": evidence}],
            "mailbox": {"drafts": evidence.get("drafts", []),
                        "sent": evidence.get("sent", []),
                        "reminders": evidence.get("reminders", []),
                        "actions": evidence.get("actions", [])},
        }

    @staticmethod
    def outcome_spec(event_id):
        source = json.loads((Path(__file__).resolve().parents[1] / "sample_workload.json").read_text())
        event = next(item for item in source["events"] if item["id"] == event_id)
        return {"events": [event]}

    def test_checklist_has_one_row_for_every_user_message(self):
        source = json.loads((Path(__file__).resolve().parents[1] / "sample_workload.json").read_text())
        events = [{"id": item["id"], "kind": "user",
                   "received_at": "2026-09-17T10:00:00+05:30",
                   "turn_result": {"success": True}, "evidence": {}}
                  for item in source["events"] if item["kind"] == "user"]
        report = evaluate({"events": events,
                           "mailbox": {"drafts": [], "sent": [],
                                       "reminders": [], "actions": []}}, source)
        self.assertEqual(report["summary"]["messages_checked"], 55)
        self.assertEqual(len(report["rows"]), 55)

    def test_objective_draft_check_passes_and_bad_content_fails(self):
        draft = {"draft_id": "D1", "recipient_email": "alice@example.test",
                 "subject": "Friday meeting", "body": "I cannot attend the Friday meeting."}
        evidence = {"drafts": [draft], "sent": [], "reminders": [],
                    "actions": [], "conversation": [
                        {"type": "poke_reply", "timestamp": "",
                         "text": "To: alice@example.test\nSubject: Friday meeting\n\n"
                                 "I cannot attend the Friday meeting."}]}
        report = evaluate(self.outcome_workload("U09", evidence), self.outcome_spec("U09"))
        self.assertEqual(report["rows"][0]["status"], "PASS")
        evidence["drafts"][0]["body"] = "Unrelated message"
        report = evaluate(self.outcome_workload("U09", evidence), self.outcome_spec("U09"))
        self.assertEqual(report["rows"][0]["status"], "FAIL")

    def test_recipient_alone_does_not_count_as_approval_request(self):
        draft = {"draft_id": "D1", "recipient_email": "carol@example.test",
                 "subject": "Flight AI 2851",
                 "body": "AI 2851 departs T2 at 07:40 and arrives at 09:45."}
        evidence = {"drafts": [draft], "sent": [], "reminders": [], "actions": [],
                    "conversation": [{"type": "poke_reply", "timestamp": "",
                                      "text": "To: carol@example.test\nSubject: Flight AI 2851\n\n"
                                              "AI 2851 departs T2 at 07:40 and arrives at 09:45."}]}
        report = evaluate(self.outcome_workload("U25", evidence), self.outcome_spec("U25"))
        approval = next(item for item in report["rows"][0]["automatic_checks"]
                        if item["check"] == "approval requested in visible reply")
        self.assertFalse(approval["passed"])
        evidence["conversation"].append(
            {"type": "poke_reply", "timestamp": "", "text": "Would you like me to send it?"}
        )
        report = evaluate(self.outcome_workload("U25", evidence), self.outcome_spec("U25"))
        approval = next(item for item in report["rows"][0]["automatic_checks"]
                        if item["check"] == "approval requested in visible reply")
        self.assertTrue(approval["passed"])

    def test_send_must_follow_approval(self):
        draft = {"draft_id": "D1", "recipient_email": "erin@example.test",
                 "subject": "Review PR #94", "body": "Please review PR #94."}
        create = {"tool": "gmail_create_draft", "time": "2026-09-17T09:59:00+05:30",
                  "arguments": {}, "result": draft}
        send = {"tool": "gmail_execute_draft", "time": "2026-09-17T10:01:00+05:30",
                "arguments": {"draft_id": "D1"},
                "result": {"status": "sent", "draft_id": "D1"}}
        sent = [{**draft, "event_id": "U40"}]
        evidence = {"drafts": [], "sent": sent, "reminders": [],
                    "actions": [create, send], "conversation": []}
        workload = self.outcome_workload("U40", evidence,
                                         "2026-09-17T10:00:00+05:30")
        report = evaluate(workload, self.outcome_spec("U40"))
        self.assertEqual(report["rows"][0]["status"], "PASS")
        send["time"] = "2026-09-17T09:59:30+05:30"
        report = evaluate(workload, self.outcome_spec("U40"))
        self.assertEqual(report["rows"][0]["status"], "FAIL")

    def test_natural_language_answer_requires_manual_review(self):
        evidence = {"drafts": [], "sent": [], "reminders": [], "actions": [],
                    "conversation": [{"type": "poke_reply", "timestamp": "",
                                      "text": "I am OpenPoke."}]}
        report = evaluate(self.outcome_workload("U01", evidence), self.outcome_spec("U01"))
        self.assertEqual(report["rows"][0]["status"], "MANUAL_REVIEW")
        self.assertEqual(report["rows"][0]["visible_replies"], ["I am OpenPoke."])

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
