"""Which model answers, and through which provider.

A model is named `provider:model` (`anthropic:claude-sonnet-5-5`,
`openrouter:deepseek/deepseek-v4-flash`); a bare id stays on the current provider, so
OpenRouter ids that contain a colon (`...:free`) still work. One client per provider is
created on first use and kept for the session."""

from __future__ import annotations

from dataclasses import dataclass

from tarjuman import App, Provider, catalog, providers

from .agent import Agent

# how providers that show the calling app (OpenRouter) name Diwan
APP = App("Diwan", "https://github.com/Zine-Elabidine/diwan")


@dataclass(frozen=True)
class Ref:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def parse(text: str, default_provider: str) -> Ref:
    head, sep, tail = text.strip().partition(":")
    if sep and tail and head in providers.names():
        return Ref(head, tail)
    return Ref(default_provider, text.strip())


class Router:
    def __init__(self, default_provider: str, base_url: str | None = None):
        """`base_url` points the default provider elsewhere (a local server, a gateway)."""
        self.default = default_provider
        self.base_url = base_url
        self._clients: dict[str, Provider] = {}

    def client(self, name: str) -> Provider:
        """Raises TarjumanError if the provider is unknown or its key is missing."""
        if name not in self._clients:
            url = self.base_url if name == self.default else None
            self._clients[name] = providers.connect(name, base_url=url, app=APP)
        return self._clients[name]

    def ref(self, text: str) -> Ref:
        return parse(text, self.default)


def describe(ref: Ref) -> str:
    """One line about a model from the catalog: context, output limit, price."""
    client_catalog = (providers.providers_table().get(ref.provider) or {}).get("catalog")
    info = catalog.lookup(client_catalog, ref.model) if client_catalog else None
    if info is None:
        return f"{ref}  (not in the catalog)"
    parts = [str(ref)]
    if info.context:
        parts.append(f"{_tokens(info.context)} context")
    if info.reasoning:
        parts.append(f"reasoning: {', '.join(info.levels)}" if info.levels else "reasoning")
    if info.price:
        parts.append(f"${info.price.input:g} in / ${info.price.output:g} out per M")
    if info.status:
        parts.append(info.status)
    return " · ".join(parts)


def switch(agent: Agent, router: Router, text: str) -> Ref:
    """Move the running conversation to another model. Raises TarjumanError (unknown
    provider, missing key) and leaves the agent unchanged in that case."""
    ref = router.ref(text)
    client = router.client(ref.provider)
    agent.use(client, ref.model)
    return ref


def listing(query: str, default_provider: str, limit: int = 20) -> list[str]:
    """`/models [provider] [text]`: catalog models matching the text, cheapest first."""
    words = query.split()
    provider = words.pop(0) if words and words[0] in providers.names() else default_provider
    needle = " ".join(words).lower()
    cat = (providers.providers_table().get(provider) or {}).get("catalog")
    if not cat:
        return [f"{provider} has no catalog: any model id its server accepts will work"]
    found = []
    for model_id in catalog.models(cat):
        info = catalog.lookup(cat, model_id)
        if needle in model_id.lower() and info and info.tools and info.status != "deprecated":
            found.append(((info.price.input if info.price else 1e9), describe(Ref(provider, model_id))))
    found.sort()
    lines = [line for _, line in found[:limit]]
    if len(found) > limit:
        lines.append(f"... {len(found) - limit} more; add words to narrow it")
    return lines or [f"no {provider} model matches {needle!r}"]


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{n // 1000}k"
