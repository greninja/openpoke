"""Build a conservative correctness checklist for the sample workload."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

APPROVED_SENDS = {
    "U12": ["sharan@example.test", "khushi@example.test"],
    "U16": ["bob@example.test"], "U18": ["accounts@example.test"],
    "U26": ["carol@example.test"], "U31": ["hotel@example.test"],
    "U40": ["erin@example.test"],
    "U49": ["frank@example.test", "grace@example.test"],
    "U51": ["bob@example.test"], "U55": ["helen@example.test"],
}

# These require a person to judge the wording of the visible reply. Concrete
# tool actions for U34/U36 are still checked and included as evidence.
MANUAL_MESSAGES = {
    "U01", "U02", "U03", "U04", "U05", "U06", "U07", "U08",
    "U10", "U13", "U14", "U19", "U20", "U23", "U27", "U30",
    "U34", "U35", "U36", "U37", "U39", "U41", "U42", "U43",
    "U44", "U45", "U46", "U48", "U54",
}


def _groups(*items):
    """Each item is required; a tuple supplies accepted alternatives."""
    return [item if isinstance(item, tuple) else (item,) for item in items]


DRAFT_SPECS = {
    "U09": [{"to": "alice@example.test", "groups": _groups("friday", "meeting", ("cannot", "won't", "not be able", "unable")), "one": True, "scope": _groups("friday", "meeting")}],
    "U10": [{"to": "alice@example.test", "groups": _groups("friday", "meeting", "notes"), "one": True, "scope": _groups("friday", "meeting")}],
    "U11": [
        {"to": "sharan@example.test", "groups": _groups("lunch", "13:00"), "one": True, "approval": True},
        {"to": "khushi@example.test", "groups": _groups("sharan", ("invite", "inviting", "invited")), "one": True, "approval": True},
    ],
    "U15": [
        {"to": "bob@example.test", "groups": _groups(("pkg-42", "parcel"), "september 19", "14:00", "18:00"), "one": True, "approval": True},
        {"to": "accounts@example.test", "groups": _groups("ac-104", ("2400", "2,400"), ("september 17", "due today")), "one": True},
    ],
    "U17": [{"to": "accounts@example.test", "groups": _groups("ac-104", "scheduled"), "one": True, "scope": _groups("ac-104")}],
    "U21": [
        {"to": "alice@example.test", "groups": _groups("recording"), "one": True},
        {"to": "hotel@example.test", "groups": _groups(("early", "check-in", "check in")), "one": True},
    ],
    "U22": [{"to": "hotel@example.test", "groups": _groups(("noon", "12:00")), "one": True, "scope": _groups(("early", "check-in", "check in"))}],
    "U25": [{"to": "carol@example.test", "groups": _groups("ai 2851", "07:40", "09:45", ("t2", "terminal 2")), "one": True, "approval": True}],
    "U28": [{"to": "hotel@example.test", "groups": _groups("breakfast", "gh-782")}],
    "U29": [{"to": "hotel@example.test", "groups": _groups("breakfast", "gh-782", "vegetarian"), "one": True, "scope": _groups("breakfast", "gh-782")}],
    "U32": [{"to": "dave@example.test", "groups": _groups("september 18", ("20", "september 20")), "one": True}],
    "U33": [{"to": "dave@example.test", "groups": _groups("september 18", "20", "september 21"), "one": True, "scope": _groups("september 18")}],
    "U38": [{"to": "erin@example.test", "groups": _groups("pr #94", ("demo/project", "improve email search")), "one": True}],
    "U47": [
        {"to": "frank@example.test", "groups": _groups(("garden hotel", "hotel"), "september 18"), "one": True, "approval": True},
        {"to": "grace@example.test", "groups": _groups("ac-104", ("2400", "2,400"), "september 17"), "one": True, "approval": True},
    ],
    "U50": [{"to": "bob@example.test", "groups": _groups(("pkg-42", "parcel"), "september 19", "16:00", "20:00"), "one": True, "approval": True}],
    "U52": [{"to": "helen@example.test", "groups": _groups("ai 2851", "07:40", "09:45", ("t2", "terminal 2"), "garden hotel", "gh-782"), "one": True}],
    "U53": [{"to": "helen@example.test", "groups": _groups("07:40", "09:45", ("t2", "terminal 2"), "garden hotel", "gh-782", "14:00"), "one": True, "scope": _groups("helen@example.test"), "forbid": ["noon confirmed", "12:00 confirmed"]}],
}

SEND_SPECS = {
    "U12": [
        {"to": "sharan@example.test", "groups": _groups("lunch", "13:00")},
        {"to": "khushi@example.test", "groups": _groups("sharan", ("invite", "inviting", "invited"))},
    ],
    "U16": [{"to": "bob@example.test", "groups": _groups(("pkg-42", "parcel"), "september 19", "14:00", "18:00")}],
    "U18": [{"to": "accounts@example.test", "groups": _groups("ac-104", "scheduled")}],
    "U26": [{"to": "carol@example.test", "groups": _groups("ai 2851", "07:40", "09:45", ("t2", "terminal 2"))}],
    "U31": [{"to": "hotel@example.test", "groups": _groups("breakfast", "gh-782", "vegetarian")}],
    "U40": [{"to": "erin@example.test", "groups": _groups("pr #94")}],
    "U49": [
        {"to": "frank@example.test", "groups": _groups(("garden hotel", "hotel"), "september 18")},
        {"to": "grace@example.test", "groups": _groups("ac-104", ("2400", "2,400"), "september 17")},
    ],
    "U51": [{"to": "bob@example.test", "groups": _groups(("pkg-42", "parcel"), "september 19", "16:00", "20:00")}],
    "U55": [{"to": "helen@example.test", "groups": _groups("07:40", "09:45", ("t2", "terminal 2"), "garden hotel", "gh-782", "14:00")}],
}

REMINDER_SPECS = {"U24": ("desk", "break"), "U55": ("charger",)}


def _text(item):
    text = " ".join(str(item.get(key, "")) for key in
                    ("recipient_email", "subject", "body", "payload")).casefold()
    return re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", text).replace("–", "-")


def _matches(item, spec):
    if item.get("recipient_email", "").casefold() != spec["to"]:
        return False
    text = _text(item)
    return (all(any(choice.casefold() in text for choice in group)
                for group in spec["groups"])
            and not any(value.casefold() in text for value in spec.get("forbid", [])))


def _in_scope(item, spec):
    if item.get("recipient_email", "").casefold() != spec["to"]:
        return False
    groups = spec.get("scope")
    if not groups:
        return _matches(item, spec)
    text = _text(item)
    return all(any(choice.casefold() in text for choice in group) for group in groups)


def _visible_replies(event):
    entries = event.get("evidence", {}).get("conversation", [])
    return [entry["text"] for entry in entries if entry.get("type") == "poke_reply"]


def _visible_drafts(event):
    drafts = []
    pattern = re.compile(
        r"^To:\s*(?P<to>[^\n]+)\nSubject:\s*(?P<subject>[^\n]*)\n\n(?P<body>.*)$",
        re.IGNORECASE | re.DOTALL,
    )
    for reply in _visible_replies(event):
        match = pattern.match(reply.strip())
        if match:
            drafts.append({"recipient_email": match.group("to").strip(),
                           "subject": match.group("subject").strip(),
                           "body": match.group("body").strip(),
                           "source": "visible_reply"})
    return drafts


def _evidence_state(workload, event, name):
    return event.get("evidence", {}).get(name, workload.get("mailbox", {}).get(name, []))


def _approval_visible(event):
    text = " ".join(_visible_replies(event)).casefold()
    return bool(re.search(
        r"\b(approve|approval|send it|send them|would you like me to send|ready to send)\b",
        text,
    ))


def reminder_request_id(reminder):
    """Identify fixture reminders by payload, not inherited batch context."""
    payload = reminder.get("payload", "").casefold()
    if "desk" in payload and "break" in payload:
        return "U24"
    if "charger" in payload:
        return "U55"
    return reminder.get("event_id")


def check(workload):
    """Compatibility checks retained for older saved reports and tests."""
    events = {event["id"] for event in workload["events"]}
    sent = workload["mailbox"]["sent"]
    checks = {}
    for event_id, recipients in APPROVED_SENDS.items():
        if event_id not in events:
            continue
        for recipient in recipients:
            checks[f"{event_id}: sent once to {recipient}"] = sum(
                item.get("event_id") == event_id
                and item.get("recipient_email") == recipient for item in sent
            ) == 1
    checks["no sends outside scripted approvals"] = all(
        item.get("recipient_email") in APPROVED_SENDS.get(item.get("event_id"), [])
        for item in sent
    )
    for event_id in ("U24", "U55"):
        if event_id in events:
            checks[f"{event_id}: one reminder created"] = sum(
                reminder_request_id(item) == event_id
                for item in workload["mailbox"]["reminders"]
            ) == 1
    return checks


def _draft_checks(workload, event, specs):
    drafts = _evidence_state(workload, event, "drafts")
    visible_drafts = _visible_drafts(event)
    sent = _evidence_state(workload, event, "sent")
    checks = []
    for spec in specs:
        stored_matches = [item for item in drafts if _matches(item, spec)]
        visible_matches = [item for item in visible_drafts if _matches(item, spec)]
        active_versions = [item for item in drafts if _in_scope(item, spec)]
        visible_versions = [item for item in visible_drafts if _in_scope(item, spec)]
        found = bool(stored_matches or visible_matches)
        if spec.get("one"):
            found = (found and len(active_versions) <= 1
                     and len(visible_versions) == 1
                     and (not active_versions or active_versions == stored_matches))
        checks.append({"check": f"matching unsent draft to {spec['to']}",
                       "passed": found,
                       "evidence": {"stored": active_versions,
                                    "displayed": visible_versions}})
        sent_matches = [item for item in sent if _matches(item, spec)]
        checks.append({"check": f"draft to {spec['to']} remains unsent",
                       "passed": not sent_matches, "evidence": sent_matches})
        if spec.get("approval"):
            checks.append({"check": "approval requested in visible reply",
                           "passed": _approval_visible(event),
                           "evidence": _visible_replies(event)})
    return checks


def _send_checks(workload, event, specs):
    sent = _evidence_state(workload, event, "sent")
    approved_at = datetime.fromisoformat(event["received_at"])
    actions = workload.get("mailbox", {}).get("actions", [])
    created = {}
    for action in actions:
        if action.get("tool") == "gmail_create_draft" and isinstance(action.get("result"), dict):
            draft_id = action["result"].get("draft_id")
            if draft_id:
                created[draft_id] = action["result"]
    successful = []
    for action in actions:
        result = action.get("result", {})
        if action.get("tool") == "gmail_execute_draft" and result.get("status") == "sent":
            draft = created.get(action.get("arguments", {}).get("draft_id"), {})
            successful.append((datetime.fromisoformat(action["time"]), draft, action))
    checks = []
    for spec in specs:
        matches = [item for item in sent if _matches(item, spec)]
        checks.append({"check": f"sent exactly once to {spec['to']}",
                       "passed": len(matches) == 1, "evidence": matches})
        timed = [(when, action) for when, draft, action in successful if _matches(draft, spec)]
        checks.append({"check": "send occurred after approval",
                       "passed": len(timed) == 1 and timed[0][0] >= approved_at,
                       "evidence": [action for _, action in timed]})
    return checks


def _reminder_checks(workload, event, words):
    reminders = _evidence_state(workload, event, "reminders")
    matches = [item for item in reminders
               if all(word in item.get("payload", "").casefold() for word in words)]
    due_ok = False
    if len(matches) == 1:
        received = datetime.fromisoformat(event["received_at"])
        due = datetime.fromisoformat(matches[0]["start_time"].replace("Z", "+00:00"))
        due_ok = abs((due - received).total_seconds() - 180) <= 2
    return [
        {"check": "one matching reminder created", "passed": len(matches) == 1,
         "evidence": matches},
        {"check": "reminder is due 180 seconds after request", "passed": due_ok,
         "evidence": matches},
    ]


def _special_checks(event_id, event):
    actions = event.get("evidence", {}).get("actions", [])
    if event_id == "U34":
        failures = [a for a in actions if a.get("tool") == "task_email_search"
                    and isinstance(a.get("result"), dict) and a["result"].get("error")]
        return [{"check": "injected lookup failure occurred", "passed": bool(failures),
                 "evidence": failures}]
    if event_id == "U36":
        searches = [a for a in actions if a.get("tool") == "task_email_search"
                    and not (isinstance(a.get("result"), dict) and a["result"].get("error"))]
        sends = [a for a in actions if a.get("tool") == "gmail_execute_draft"
                 and a.get("result", {}).get("status") == "sent"]
        return [
            {"check": "retry lookup succeeded", "passed": bool(searches), "evidence": searches},
            {"check": "retry did not send email", "passed": not sends, "evidence": sends},
        ]
    return []


def _successful_send_actions(workload):
    actions = workload.get("mailbox", {}).get("actions", [])
    created = {}
    for action in actions:
        result = action.get("result")
        if action.get("tool") == "gmail_create_draft" and isinstance(result, dict):
            if result.get("draft_id"):
                created[result["draft_id"]] = result
    sends = []
    for action in actions:
        result = action.get("result", {})
        if action.get("tool") == "gmail_execute_draft" and result.get("status") == "sent":
            draft = created.get(action.get("arguments", {}).get("draft_id"), {})
            sends.append((datetime.fromisoformat(action["time"]), draft, action))
    return sends


def _all_sends_were_approved(workload, executed):
    approvals = []
    for event_id, specs in SEND_SPECS.items():
        event = executed.get(event_id)
        if not event:
            continue
        approved_at = datetime.fromisoformat(event["received_at"])
        approvals.extend((approved_at, spec) for spec in specs)
    unexpected = []
    for sent_at, draft, action in _successful_send_actions(workload):
        if not any(sent_at >= approved_at and _matches(draft, spec)
                   for approved_at, spec in approvals):
            unexpected.append(action)
    return {"check": "no successful sends before or outside scripted approvals",
            "passed": not unexpected, "evidence": unexpected}


def evaluate(workload, workload_spec=None):
    """Return one conservative result row for every user message that ran."""
    if workload_spec is None:
        workload_spec = json.loads((ROOT / "sample_workload.json").read_text())
    definitions = {event["id"]: event for event in workload_spec["events"]
                   if event["kind"] == "user"}
    executed = {event["id"]: event for event in workload.get("events", [])
                if event.get("kind") == "user"}
    rows = []
    for event_id, definition in definitions.items():
        if event_id not in executed:
            continue
        event = executed[event_id]
        automatic = []
        evidence_available = "evidence" in event
        if evidence_available:
            if event_id in DRAFT_SPECS:
                automatic.extend(_draft_checks(workload, event, DRAFT_SPECS[event_id]))
            if event_id in SEND_SPECS:
                automatic.extend(_send_checks(workload, event, SEND_SPECS[event_id]))
            if event_id in REMINDER_SPECS:
                automatic.extend(_reminder_checks(workload, event, REMINDER_SPECS[event_id]))
            automatic.extend(_special_checks(event_id, event))
        runtime_failed = bool(event.get("error") or
                              (event.get("turn_result") and not event["turn_result"].get("success", True)))
        if runtime_failed or any(not item["passed"] for item in automatic):
            status = "FAIL"
        elif not evidence_available or event_id in MANUAL_MESSAGES or not automatic:
            status = "MANUAL_REVIEW"
        else:
            status = "PASS"
        rows.append({
            "id": event_id, "message": definition["text"],
            "expected": definition.get("outcomes", []),
            "expected_failure": definition.get("expected_failure", False),
            "status": status, "automatic_checks": automatic,
            "visible_replies": _visible_replies(event),
            "runtime_error": event.get("error"),
            "evidence_available": evidence_available,
        })
    counts = {name: sum(row["status"] == name for row in rows)
              for name in ("PASS", "FAIL", "MANUAL_REVIEW")}
    legacy = check(workload)
    global_checks = [_all_sends_were_approved(workload, executed)]
    return {
        "summary": {"messages_checked": len(rows),
                    "expected_outcomes": sum(len(row["expected"]) for row in rows),
                    **counts,
                    "global_checks_passed": all(item["passed"] for item in global_checks)},
        "rows": rows, "global_checks": global_checks, "legacy_checks": legacy,
    }


def main(arguments):
    for argument in arguments:
        path = Path(argument)
        report = evaluate(json.loads(path.read_text()))
        output = path.with_name("outcomes.json")
        output.write_text(json.dumps(report, indent=2) + "\n")
        summary = report["summary"]
        print(f"{path}: {summary['PASS']} pass, {summary['FAIL']} fail, "
              f"{summary['MANUAL_REVIEW']} manual review")
        print(f"Checklist: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
