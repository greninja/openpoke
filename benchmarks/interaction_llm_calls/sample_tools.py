"""Small, local tool adapter for the sample workload. No external delivery."""

from contextvars import ContextVar
from datetime import datetime
from functools import partial
import re
from check_outcomes import reminder_request_id


current_event = ContextVar("benchmark_event", default="unknown")


class Mailbox:
    def __init__(self, fixture):
        self.now = datetime.fromisoformat(fixture["initial_clock"])
        self.mail = list(fixture["initial_mail"])
        self.drafts = {}
        self.sent = []
        self.reminders = []
        self.actions = []
        self.next_draft = 1
        self.contacts = set()
        for mail in self.mail:
            self.observe(mail["sender"])

    def observe(self, text):
        self.contacts.update(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", text))

    def registry(self, agent_name, schemas):
        # Replace every execution tool, never fall through to real services.
        return {schema["function"]["name"]: partial(
            self.call, schema["function"]["name"], agent_name
        ) for schema in schemas}

    def call(self, tool, agent, **args):
        action = {"event_id": current_event.get(), "tool": tool, "agent": agent,
                  "time": self.now.isoformat(), "arguments": args}
        self.actions.append(action)
        result = self._call(tool, agent, args)
        action["result"] = result
        return result

    def _call(self, tool, agent, args):
        if tool in {"gmail_search_people", "gmail_get_people", "gmail_get_contacts"}:
            query = args.get("query", "").casefold()
            people = [{"names": [{"displayName": address.split("@")[0]}],
                       "emailAddresses": [{"value": address}]}
                      for address in sorted(self.contacts) if query in address.casefold()]
            return {"people": people, "connections": people}
        if tool == "task_email_search":
            if current_event.get() == "U34":
                return {"error": "Injected invoice lookup failure"}
            # Return the small visible fixture, leaving relevance selection to
            # the execution LLM. This is not a Gmail search benchmark.
            return [{**mail, "thread_id": mail["id"], "recipient": "user@example.test",
                     "timestamp": mail["received_at"], "clean_text": mail["body"],
                     "query": args.get("search_query", "")} for mail in self.mail]
        if tool == "gmail_create_draft":
            draft_id = f"D{self.next_draft}"
            self.next_draft += 1
            draft = {"id": draft_id, "draft_id": draft_id, **args}
            self.drafts[draft_id] = draft
            return dict(draft)
        if tool in {"gmail_execute_draft", "gmail_delete_draft"}:
            draft = self.drafts.pop(args["draft_id"], None)
            if draft is None:
                return {"error": "Draft not found"}
            if tool == "gmail_execute_draft":
                self.sent.append({**draft, "event_id": current_event.get()})
            return {"status": "sent" if tool == "gmail_execute_draft" else "deleted",
                    "draft_id": args["draft_id"]}
        if tool == "gmail_list_drafts":
            return {"drafts": list(self.drafts.values())}
        if tool in {"gmail_reply_to_thread", "gmail_forward_email"}:
            sent = {**args, "id": f"S{len(self.sent) + 1}", "event_id": current_event.get()}
            self.sent.append(sent)
            return {"status": "sent", **sent}
        if tool == "createTrigger":
            reminder = {**args, "id": len(self.reminders) + 1, "agent": agent,
                        "event_id": current_event.get(), "status": args.get("status") or "active",
                        "start_time": args.get("start_time") or self.now.isoformat()}
            self.reminders.append(reminder)
            return {**reminder, "trigger_id": reminder["id"], "next_trigger": reminder["start_time"]}
        if tool == "listTriggers":
            return {"triggers": [dict(r) for r in self.reminders if r["agent"] == agent]}
        if tool == "updateTrigger":
            reminder = next((r for r in self.reminders
                             if r["id"] == int(args["trigger_id"]) and r["agent"] == agent), None)
            if reminder is None:
                return {"error": "Reminder not found"}
            reminder.update({k: v for k, v in args.items() if k != "trigger_id"})
            return dict(reminder)
        return {"error": f"Tool {tool} is not implemented in the sample mailbox"}

    def deliver(self, event, fixture):
        for incoming in fixture["incoming_mail"]:
            if incoming["visible_when"] == event["id"]:
                self.mail.append({"id": incoming["id"], "received_at": self.now.isoformat(),
                                  "sender": "notification@example.test",
                                  "subject": event["text"], "body": event["text"]})
        outcome_id = event["release"].get("advance_clock_to_reminder")
        if outcome_id:
            source = outcome_id.split(".")[0]
            matches = [r for r in self.reminders if reminder_request_id(r) == source]
            if len(matches) != 1:
                if matches:
                    raise RuntimeError(f"Cannot deliver {event['id']}: multiple reminders match {source}")
                raise RuntimeError(f"Cannot deliver {event['id']}: reminder from {source} was not created")
            reminder = matches[0]
            due = datetime.fromisoformat(reminder["start_time"].replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=self.now.tzinfo)
            self.now = max(self.now, due)
            reminder["status"] = "completed"

    def snapshot(self):
        return {"clock": self.now.isoformat(), "mail": self.mail,
                "drafts": list(self.drafts.values()), "sent": self.sent,
                "reminders": self.reminders, "actions": self.actions}
