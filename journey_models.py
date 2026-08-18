"""
journey_models.py
==================
Feature 2 (Customer Journey Monitoring): plain data structures.

Kept deliberately separate from journey.py (orchestration) and
playwright_runner.py (browser mechanics) so the shape of a journey result
is independent of how it was produced — this is what "support future
journey steps" (an explicit requirement) actually depends on: adding a new
step type only ever means adding a new `StepResult`, never changing this
file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class StepResult:
    """The outcome of one journey step (e.g. "add_to_cart")."""
    step_name: str
    success: bool = False
    http_status: Optional[int] = None
    page_title: str = ""
    load_time_seconds: Optional[float] = None
    console_errors: list[str] = field(default_factory=list)
    js_errors: list[str] = field(default_factory=list)
    network_failures: list[str] = field(default_factory=list)
    broken_images: list[str] = field(default_factory=list)
    screenshot_path: Optional[str] = None
    error_message: str = ""
    retried: int = 0
    permanent_failure: bool = False  # True = a missing element, not a transient error


@dataclass
class JourneyResult:
    """The outcome of one full funnel run (homepage -> checkout) for one product."""
    product_name: str
    product_url: str
    started_at: str
    finished_at: str = ""
    success: bool = False
    failed_step: Optional[str] = None
    total_duration_seconds: float = 0.0
    steps: list[StepResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "product_name": self.product_name,
            "product_url": self.product_url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "success": self.success,
            "failed_step": self.failed_step,
            "total_duration_seconds": self.total_duration_seconds,
            "steps": [asdict(s) for s in self.steps],
        }

    @property
    def all_console_errors(self) -> list[str]:
        return [e for s in self.steps for e in s.console_errors]

    @property
    def all_js_errors(self) -> list[str]:
        return [e for s in self.steps for e in s.js_errors]

    @property
    def all_network_failures(self) -> list[str]:
        return [e for s in self.steps for e in s.network_failures]

    @property
    def all_broken_images(self) -> list[str]:
        return [e for s in self.steps for e in s.broken_images]

    @property
    def had_screenshot(self) -> bool:
        return any(s.screenshot_path for s in self.steps)
