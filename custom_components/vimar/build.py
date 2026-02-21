"""Build/version information for the integration.

This is intentionally separate from manifest.json so we can log a deterministic
build identifier even while iterating on branches.

Format: YYYY.MM.DD.BUILD (monotonic within the day)
"""

from __future__ import annotations

BUILD_VERSION = "2026.02.21.001"
