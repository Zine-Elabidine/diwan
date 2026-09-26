#!/usr/bin/env bash
# Shallow-clone the reference runtimes into refs/ (gitignored).
# Commits noted in docs/reading-list.md are what the notes were written against.
set -euo pipefail
cd "$(dirname "$0")/../refs" 2>/dev/null || { mkdir -p "$(dirname "$0")/../refs"; cd "$(dirname "$0")/../refs"; }
while read -r name repo; do
  [ -d "$name" ] && { echo "skip $name"; continue; }
  git clone -q --depth 1 "https://github.com/$repo.git" "$name" && echo "ok   $name" &
done <<LIST
mini-swe-agent       SWE-agent/mini-swe-agent
pi-mono              badlogic/pi-mono
smolagents           huggingface/smolagents
openai-agents-python openai/openai-agents-python
pydantic-ai          pydantic/pydantic-ai
strands-agents       strands-agents/sdk-python
langgraph            langchain-ai/langgraph
claude-agent-sdk-python anthropics/claude-agent-sdk-python
openhands-sdk        OpenHands/software-agent-sdk
aider                Aider-AI/aider
opencode             sst/opencode
gemini-cli           google-gemini/gemini-cli
codex                openai/codex
goose                block/goose
LIST
wait
