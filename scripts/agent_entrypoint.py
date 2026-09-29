"""Zip-root entrypoint shim for AgentCore Runtime direct code deployment.

`scripts/build_agent.sh` copies this file to `<build_dir>/main.py` (the zip
root) while the actual `src/agent` package is copied to `<build_dir>/agent/`
(preserving `agent`'s own `from agent.x import y` sibling imports). This
keeps `EntryPoint: ["main.py"]` (`infra/stacks/agent_stack.py`) pointed at a
file that exists at the zip root, while the real `BedrockAgentCoreApp` and
`@app.entrypoint`-decorated handler live in `agent.main`.

Verified against AWS documentation before writing `scripts/build_agent.sh`
(see its header for the cited URL): direct code deployment requires both the
dependencies and the entry file at the zip root.
"""

from __future__ import annotations

from agent.main import app

if __name__ == "__main__":
    app.run()
