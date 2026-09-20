"""One interface, three backends: the choice between them, and the two tool loops.

Nothing here needs a credential: the Anthropic loop runs against a fake client and
the OpenRouter loop against a mock transport. test_model_live.py makes real calls.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from in2lambda_agent.model import (
    MAX_TOOL_ROUNDS,
    OPENROUTER_URL,
    AgentSDKBackend,
    AnthropicBackend,
    ModelUnavailable,
    OpenRouterBackend,
    Tool,
    ToolCall,
    choose_backend,
)
from in2lambda_agent.settings import Settings

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


def test_no_key_means_the_claude_code_login():
    assert choose_backend(Settings()).name == "agent-sdk"


def test_an_anthropic_key_wins():
    settings = Settings(anthropic_api_key="a", openrouter_api_key="o")
    assert choose_backend(settings).name == "anthropic"


def test_an_openrouter_key_alone_is_used():
    settings = Settings(openrouter_api_key="o")
    assert choose_backend(settings).name == "openrouter"


@pytest.mark.parametrize(
    "backend, variable",
    [
        (AnthropicBackend(None), "ANTHROPIC_API_KEY"),
        (OpenRouterBackend(None), "OPENROUTER_API_KEY"),
    ],
)
def test_a_backend_without_its_key_says_which_to_set(backend, variable):
    assert variable in backend.unavailable()
    with pytest.raises(ModelUnavailable, match=variable):
        backend.call("system", "prompt", [ADD])


def test_the_agent_sdk_without_claude_code_says_to_log_in(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    backend = AgentSDKBackend()

    assert "claude login" in backend.unavailable()
    with pytest.raises(ModelUnavailable, match="claude login"):
        backend.call("system", "prompt")


class FakeAnthropic:
    """Returns the given responses in turn, repeating the last one for ever.

    Repeating is how a model that never stops asking for tools is written.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def anthropic_turn(*content, input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        content=list(content),
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens
        ),
    )


ANTHROPIC_TOOL_USE = SimpleNamespace(
    type="tool_use", id="tu_1", name="add", input={"a": 2, "b": 3}
)


def test_the_anthropic_loop_runs_a_tool_and_returns_the_answer():
    client = FakeAnthropic(
        anthropic_turn(ANTHROPIC_TOOL_USE),
        anthropic_turn(SimpleNamespace(type="text", text="5")),
    )
    backend = AnthropicBackend(None, client=client)

    reply = backend.call("Answer briefly.", "Add 2 and 3.", [ADD])

    assert reply.text == "5"
    assert reply.calls == [ToolCall("add", {"a": 2, "b": 3}, "5")]
    assert reply.backend == "anthropic"
    # Both turns counted, not just the last.
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (20, 10)
    assert reply.usage.seconds > 0

    # The tool's result went back to the model, against the right tool_use.
    assert client.requests[0]["tools"][0]["name"] == "add"
    assert client.requests[0]["system"] == "Answer briefly."
    assert client.requests[1]["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "5"}
        ],
    }


def test_the_anthropic_loop_stops_a_model_that_never_answers():
    client = FakeAnthropic(anthropic_turn(ANTHROPIC_TOOL_USE))
    backend = AnthropicBackend(None, client=client)

    with pytest.raises(RuntimeError, match="without answering"):
        backend.call("system", "prompt", [ADD])

    assert len(client.requests) == MAX_TOOL_ROUNDS


def openrouter_client(*bodies):
    """An httpx.Client answering with the given bodies in turn, then the last.

    Returns:
        The client, and the list its requests are recorded in.
    """
    requests = []
    remaining = list(bodies)

    def handler(request):
        requests.append(request)
        body = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def openrouter_body(message, prompt_tokens=10, completion_tokens=5):
    return {
        "choices": [{"message": message}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


OPENROUTER_TOOL_CALL = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
        }
    ],
}


def test_the_openrouter_loop_runs_a_tool_and_returns_the_answer():
    client, requests = openrouter_client(
        openrouter_body(OPENROUTER_TOOL_CALL),
        openrouter_body({"role": "assistant", "content": "5"}),
    )
    backend = OpenRouterBackend("or-key", client=client)

    reply = backend.call("Answer briefly.", "Add 2 and 3.", [ADD])

    assert reply.text == "5"
    assert reply.calls == [ToolCall("add", {"a": 2, "b": 3}, "5")]
    assert reply.backend == "openrouter"
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (20, 10)
    assert reply.usage.seconds > 0

    assert str(requests[0].url) == OPENROUTER_URL
    assert requests[0].headers["Authorization"] == "Bearer or-key"
    sent = json.loads(requests[1].content)
    assert sent["tools"][0]["function"]["name"] == "add"
    assert sent["messages"][0] == {"role": "system", "content": "Answer briefly."}
    assert sent["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "5",
    }


def test_the_openrouter_loop_stops_a_model_that_never_answers():
    client, requests = openrouter_client(openrouter_body(OPENROUTER_TOOL_CALL))
    backend = OpenRouterBackend("or-key", client=client)

    with pytest.raises(RuntimeError, match="without answering"):
        backend.call("system", "prompt", [ADD])

    assert len(requests) == MAX_TOOL_ROUNDS
