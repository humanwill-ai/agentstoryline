#!/usr/bin/env python3
"""Compatibility entry point for AgentStoryline; use agentstoryline.py for new commands."""
from agentstoryline import main, write_json_new


if __name__ == "__main__":
    raise SystemExit(main())
