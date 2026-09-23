"""One real call through every backend that has credentials on this machine.

Opt-in: every backend skips unless IN2LAMBDA_AGENT_LIVE=1 is set, so an ordinary
`pytest` run spends no model call. With it set, a backend that has no credentials
skips with what to set or do instead.
"""

import os

import pytest

from in2lambda_agent.model import (
    AgentSDKBackend,
    AnthropicBackend,
    OpenRouterBackend,
    Tool,
)
from in2lambda_agent.settings import load_settings

ADD = Tool(
    name="add",
    description="Add two integers",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
    run=lambda arguments: str(arguments["a"] + arguments["b"]),
)


def every_backend():
    """All three, whether or not they can run: each test skips for itself."""
    settings = load_settings()
    return [
        AgentSDKBackend(),
        AnthropicBackend(settings.anthropic_api_key),
        OpenRouterBackend(settings.openrouter_api_key),
    ]


@pytest.mark.parametrize(
    "backend", every_backend(), ids=lambda backend: backend.name
)
def test_a_trivial_tool_call(backend):
    if os.environ.get("IN2LAMBDA_AGENT_LIVE") != "1":
        pytest.skip("set IN2LAMBDA_AGENT_LIVE=1 to make real model calls")

    reason = backend.unavailable()
    if reason:
        pytest.skip(reason)

    reply = backend.call(
        "You answer with as few words as possible.",
        "Use the add tool on 2 and 3, then answer with just the number.",
        [ADD],
    )

    assert "5" in reply.text
    assert [call.name for call in reply.calls] == ["add"]
    assert reply.backend == backend.name
    assert reply.usage.input_tokens > 0
    assert reply.usage.output_tokens > 0
    assert reply.usage.seconds > 0
