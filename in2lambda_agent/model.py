"""One model call, with tools, behind one interface.

The agent makes three kinds of call — write a spec from the numbered source, fix
a validation report using the package's draft commands as tools, and ask what a
page shows that its OCR does not — and all three are the same shape: a system
prompt, a user prompt, some tools, some page images, one final text back. That
shape is `Backend.call`, and this is the only module that imports a provider
SDK. Callers ask `choose_backend` for a backend and never learn which one they
got.

Which one they get follows from the settings alone: an Anthropic key, else an
OpenRouter key, else the Claude Code login through the Agent SDK, which is what
development uses.

Every `Reply` carries the tokens and the wall time for that call, which the
design spec's test plan records per document.
"""

import asyncio
import base64
import json
import shutil
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Optional, Protocol, Sequence

from in2lambda_agent.settings import Settings

# The same model, named each way: Sonnet, which the corpus test plan runs over
# every document, so the cheaper of the two. Both are aliases the Messages API
# resolves to the current snapshot — `claude-sonnet-5` is one of the ids the
# installed anthropic SDK lists in `anthropic.types.model.Model`. The Agent SDK
# is left on Claude Code's own default, so that a login's configured model is
# the one that runs. Adaptive thinking is on by default, so neither request
# asks for thinking.
ANTHROPIC_MODEL = "claude-sonnet-5"
OPENROUTER_MODEL = "anthropic/claude-sonnet-5"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Stops a model that asks for a tool forever. The Agent SDK counts its own turns
# instead, so this is the limit for the two hand-written loops.
MAX_TOOL_ROUNDS = 8

MAX_OUTPUT_TOKENS = 8192

REQUEST_TIMEOUT = 300.0


@dataclass
class Tool:
    """A tool the model may call, in no provider's shape.

    Each backend translates this to its own wire format.

    Attributes:
        name: What the model calls it.
        description: What it does, for the model.
        parameters: JSON schema for the arguments object.
        run: Takes the parsed arguments, returns the result as text.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], str]


@dataclass
class Usage:
    """What one call cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


@dataclass
class ToolCall:
    """One tool the model called during a call, and what it got back."""

    name: str
    arguments: dict[str, Any]
    result: str


@dataclass
class Reply:
    """The end of one call: the final text, and the record of getting there."""

    text: str
    usage: Usage
    calls: list[ToolCall] = field(default_factory=list)
    backend: str = ""
    model: str = ""


class ModelUnavailable(RuntimeError):
    """A backend was called without the credential or the login it needs."""


def _encoded(image: bytes) -> str:
    """One PNG page as the base64 every provider's image block carries."""
    return base64.standard_b64encode(image).decode("ascii")


class Backend(Protocol):
    """What every backend does, and all a caller may rely on."""

    name: str

    def unavailable(self) -> Optional[str]:
        """Whether this backend can run.

        Returns:
            None when it can, otherwise a reason naming what to set or do.
        """

    def call(
        self,
        system: str,
        prompt: str,
        tools: Sequence[Tool] = (),
        images: Sequence[bytes] = (),
    ) -> Reply:
        """Makes one call, running any tool the model asks for.

        Args:
            system: The system prompt.
            prompt: The user prompt.
            tools: The tools the model may call.
            images: PNG pages to send before the prompt, in order. What a
                model sees of them is the model's; nothing here checks.

        Returns:
            The final text, with the tokens, the wall time and the tool calls.

        Raises:
            ModelUnavailable: If `unavailable` would give a reason.
        """


class AgentSDKBackend:
    """Claude Code's own login, through the Claude Agent SDK.

    The default: it needs no key, and in development it is the login already on
    the machine. Tools become an in-process MCP server, and Claude Code's built-in
    tools are switched off, so the model has exactly the tools it was given.
    """

    name = "agent-sdk"

    def unavailable(self) -> Optional[str]:
        """See `Backend.unavailable`."""
        # Where the SDK's own lookup for the CLI starts.
        if shutil.which("claude") is None:
            return (
                "install Claude Code and run `claude login` to use the "
                "agent-sdk backend, or set ANTHROPIC_API_KEY"
            )
        return None

    def call(
        self,
        system: str,
        prompt: str,
        tools: Sequence[Tool] = (),
        images: Sequence[bytes] = (),
    ) -> Reply:
        """See `Backend.call`."""
        reason = self.unavailable()
        if reason:
            raise ModelUnavailable(reason)
        return asyncio.run(self._call(system, prompt, tools, images))

    async def _call(
        self,
        system: str,
        prompt: str,
        tools: Sequence[Tool],
        images: Sequence[bytes] = (),
    ) -> Reply:
        from claude_agent_sdk import (
            ClaudeAgentOptions,
            ResultMessage,
            create_sdk_mcp_server,
            query,
        )
        from claude_agent_sdk import tool as sdk_tool

        calls: list[ToolCall] = []

        def wrap(one: Tool):
            async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
                result = one.run(dict(arguments))
                calls.append(ToolCall(one.name, dict(arguments), result))
                return {"content": [{"type": "text", "text": result}]}

            return sdk_tool(one.name, one.description, one.parameters)(handler)

        server = create_sdk_mcp_server(name="agent", tools=[wrap(t) for t in tools])
        options = ClaudeAgentOptions(
            system_prompt=system,
            mcp_servers={"agent": server},
            allowed_tools=[f"mcp__agent__{one.name}" for one in tools],
            # No built-in tools, and no settings file: nothing the machine
            # happens to have configured reaches the call. Both need the empty
            # list, which the SDK documents as "disable all built-in tools" and
            # "disable filesystem settings"; the default for each is `None`,
            # which loads the CLI's own set.
            tools=[],
            setting_sources=[],
            max_turns=MAX_TOOL_ROUNDS,
        )

        # The loop runs to the end, and the `finally` closes the generator if
        # anything leaves it early. Returning or raising from inside it drops
        # the generator while it is suspended at its `yield`: the loop's
        # finalizer starts an aclose(), then `asyncio.run`'s shutdown starts a
        # second one, and the unraisable hook prints "aclose(): asynchronous
        # generator is already running" before any of our own output. The
        # stream ends just after the result message, so running it out is cheap.
        # A string prompt is text and nothing else, so pages go through the
        # SDK's streaming form: one user message whose content is blocks, which
        # Claude Code passes to the API as it stands.
        asked: Any = prompt
        if images:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _encoded(image),
                    },
                }
                for image in images
            ] + [{"type": "text", "text": prompt}]

            async def one_message():
                yield {
                    "type": "user",
                    "message": {"role": "user", "content": content},
                    "parent_tool_use_id": None,
                    "session_id": "agent",
                }

            asked = one_message()

        result = None
        stream = query(prompt=asked, options=options)
        try:
            async for message in stream:
                if isinstance(message, ResultMessage) and result is None:
                    result = message
        finally:
            await stream.aclose()

        if result is None:
            raise RuntimeError("the agent-sdk backend returned no result")
        if result.is_error:
            raise RuntimeError(
                f"the agent-sdk backend stopped on {result.subtype}: "
                f"{result.result}"
            )
        usage = result.usage or {}
        return Reply(
            text=result.result or "",
            usage=Usage(
                # Almost all of this prompt is cached, and cached input is
                # still input, so the three counts go together.
                input_tokens=usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                seconds=result.duration_ms / 1000,
            ),
            calls=calls,
            backend=self.name,
            model=next(iter(result.model_usage or {}), ""),
        )


class AnthropicBackend:
    """The Anthropic API, with ANTHROPIC_API_KEY."""

    name = "anthropic"

    def __init__(
        self,
        api_key: Optional[str],
        *,
        client: Any = None,
        model: str = ANTHROPIC_MODEL,
    ) -> None:
        """Args:
        api_key: ANTHROPIC_API_KEY, or None when it is not set.
        client: An `anthropic.Anthropic` to use instead of building one.
        model: The model to ask for.
        """
        self._api_key = api_key
        self._client = client
        self.model = model

    def unavailable(self) -> Optional[str]:
        """See `Backend.unavailable`."""
        if self._client is None and not self._api_key:
            return "set ANTHROPIC_API_KEY to use the anthropic backend"
        return None

    def call(
        self,
        system: str,
        prompt: str,
        tools: Sequence[Tool] = (),
        images: Sequence[bytes] = (),
    ) -> Reply:
        """See `Backend.call`."""
        reason = self.unavailable()
        if reason:
            raise ModelUnavailable(reason)
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=self._api_key, timeout=REQUEST_TIMEOUT
            )

        by_name = {one.name: one for one in tools}
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system,
        }
        if tools:
            request["tools"] = [
                {
                    "name": one.name,
                    "description": one.description,
                    "input_schema": one.parameters,
                }
                for one in tools
            ]

        content: Any = prompt
        if images:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _encoded(image),
                    },
                }
                for image in images
            ] + [{"type": "text", "text": prompt}]

        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        usage = Usage()
        calls: list[ToolCall] = []
        started = perf_counter()

        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.messages.create(messages=messages, **request)
            usage.input_tokens += response.usage.input_tokens
            usage.output_tokens += response.usage.output_tokens

            uses = [block for block in response.content if block.type == "tool_use"]
            if not uses:
                usage.seconds = perf_counter() - started
                text = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                return Reply(text, usage, calls, self.name, self.model)

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for use in uses:
                arguments = dict(use.input)
                result = by_name[use.name].run(arguments)
                calls.append(ToolCall(use.name, arguments, result))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use.id,
                        "content": result,
                    }
                )
            messages.append({"role": "user", "content": results})

        raise RuntimeError(
            f"the {self.name} backend asked for tools for "
            f"{MAX_TOOL_ROUNDS} rounds without answering"
        )


class OpenRouterBackend:
    """OpenRouter's OpenAI-compatible API, with OPENROUTER_API_KEY.

    Ported from in2lambda's parked/llm-client branch (PR #25), which pointed an
    OpenAI client at the same endpoint. Here the request is written out, so that
    one HTTP client covers the one call this interface makes.
    """

    name = "openrouter"

    def __init__(
        self,
        api_key: Optional[str],
        *,
        client: Any = None,
        model: str = OPENROUTER_MODEL,
    ) -> None:
        """Args:
        api_key: OPENROUTER_API_KEY, or None when it is not set.
        client: An `httpx.Client` to use instead of building one.
        model: The OpenRouter model slug to use.
        """
        self._api_key = api_key
        self._client = client
        self.model = model

    def unavailable(self) -> Optional[str]:
        """See `Backend.unavailable`."""
        if not self._api_key:
            return (
                "set OPENROUTER_API_KEY, a key from https://openrouter.ai/keys, "
                "to use the openrouter backend"
            )
        return None

    def call(
        self,
        system: str,
        prompt: str,
        tools: Sequence[Tool] = (),
        images: Sequence[bytes] = (),
    ) -> Reply:
        """See `Backend.call`."""
        reason = self.unavailable()
        if reason:
            raise ModelUnavailable(reason)

        client = self._client
        if client is None:
            import httpx

            client = httpx.Client(timeout=REQUEST_TIMEOUT)
        try:
            return self._loop(client, system, prompt, tools, images)
        finally:
            if self._client is None:
                client.close()

    def _loop(
        self,
        client: Any,
        system: str,
        prompt: str,
        tools: Sequence[Tool],
        images: Sequence[bytes] = (),
    ) -> Reply:
        by_name = {one.name: one for one in tools}
        request: dict[str, Any] = {"model": self.model}
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": one.name,
                        "description": one.description,
                        "parameters": one.parameters,
                    },
                }
                for one in tools
            ]

        # OpenRouter is OpenAI-shaped, where an image is a data URL rather than
        # Anthropic's base64 block.
        content: Any = prompt
        if images:
            content = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{_encoded(image)}"
                    },
                }
                for image in images
            ] + [{"type": "text", "text": prompt}]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        usage = Usage()
        calls: list[ToolCall] = []
        started = perf_counter()

        for _ in range(MAX_TOOL_ROUNDS):
            response = client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={**request, "messages": messages},
            )
            response.raise_for_status()
            body = response.json()
            counts = body.get("usage") or {}
            usage.input_tokens += counts.get("prompt_tokens", 0)
            usage.output_tokens += counts.get("completion_tokens", 0)

            message = body["choices"][0]["message"]
            uses = message.get("tool_calls") or []
            if not uses:
                usage.seconds = perf_counter() - started
                return Reply(
                    message.get("content") or "",
                    usage,
                    calls,
                    self.name,
                    self.model,
                )

            messages.append(message)
            for use in uses:
                arguments = json.loads(use["function"]["arguments"] or "{}")
                result = by_name[use["function"]["name"]].run(arguments)
                calls.append(ToolCall(use["function"]["name"], arguments, result))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": use["id"],
                        "content": result,
                    }
                )

        raise RuntimeError(
            f"the {self.name} backend asked for tools for "
            f"{MAX_TOOL_ROUNDS} rounds without answering"
        )


def choose_backend(settings: Settings) -> Backend:
    """Picks the backend the settings call for.

    An Anthropic key wins, then an OpenRouter key; with neither, the Claude Code
    login through the Agent SDK. This is the whole of "which provider": no
    caller decides, and none needs to know.

    Args:
        settings: The environment the run has available.

    Returns:
        A backend, which may still be unavailable — ask it before calling.
    """
    if settings.anthropic_api_key:
        return AnthropicBackend(settings.anthropic_api_key)
    if settings.openrouter_api_key:
        return OpenRouterBackend(settings.openrouter_api_key)
    return AgentSDKBackend()
