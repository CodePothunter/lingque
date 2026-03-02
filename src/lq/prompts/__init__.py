"""Centralized prompt templates, tag definitions, and string constants.

All user-facing text, LLM prompt templates, and XML tag definitions are
collected here for easy maintenance and localization.  No other module
should contain hardcoded prompt text or user-facing strings.

Convention:
  - Module-level UPPER_CASE constants for direct use.
  - Templates use Python str.format() with **named** placeholders.
  - ``wrap_tag()`` for consistent XML-style tag generation.
"""

from __future__ import annotations

from lq.prompts.tags import *  # noqa: F401,F403
from lq.prompts.system import *  # noqa: F401,F403
from lq.prompts.tools import *  # noqa: F401,F403
from lq.prompts.reflection import *  # noqa: F401,F403
from lq.prompts.rl import *  # noqa: F401,F403
from lq.prompts.session import *  # noqa: F401,F403
from lq.prompts.group import *  # noqa: F401,F403
from lq.prompts.ui import *  # noqa: F401,F403
from lq.prompts.intent import *  # noqa: F401,F403
