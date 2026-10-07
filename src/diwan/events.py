"""What the loop tells the UI: its state, tool calls, retries, the context gauge, turn ends,
and the model's own stream events passed through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tarjuman import Event, TarjumanError, ToolCall, ToolResult, Usage

from .context import ContextUse

State = Literal["thinking", "running", "waiting", "idle"]
Reason = Literal["done", "max_tokens", "max_steps", "interrupted", "error"]


@dataclass
class StateChanged:
    state: State


@dataclass
class ToolStarted:
    call: ToolCall


@dataclass
class ToolFinished:
    call: ToolCall
    result: ToolResult


@dataclass
class Retrying:
    error: TarjumanError
    attempt: int
    wait: float


@dataclass
class ContextChanged:
    context: ContextUse


@dataclass
class ContextCleared:
    """Old tool outputs were cleared to save context (the log keeps them)."""
    text: str


@dataclass
class ContextSummarized:
    """The oldest messages were summarized (the log keeps them), or a summary failed."""
    text: str


@dataclass
class TurnEnded:
    reason: Reason
    steps: int
    usage: Usage
    error: str | None = None


UIEvent = (StateChanged | ToolStarted | ToolFinished | Retrying | TurnEnded | ContextChanged
           | ContextCleared | ContextSummarized | Event)
