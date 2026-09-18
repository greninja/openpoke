"""Detect described tool invocations; never execute code from model text."""

import re


def describes_tool_call(text, names):
    return any(re.search(r"\b" + re.escape(name) + r"\s*[({]", text) or
               re.search(r"\bCalling\s+" + re.escape(name) + r"\b", text)
               for name in names)
