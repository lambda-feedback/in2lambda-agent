"""The server behind the page: one run at a time, on 127.0.0.1.

A run happens in a thread of its own, and reports each stage through
`routes.convert`'s `on_stage` callback. The callback appends an event to the
run's list; `/api/events` reads that list and sends each event to the browser
over server-sent events. The list is kept from the start of the run, so a page
that connects late still receives every line.

A stream ends with the event that leaves the run idle: `done` or `error`. The
page counts the events it has read and opens the next stream with `?since=`, so
no event is read twice.

The page may fetch a file only when the server has linked to it — the zip, and
the filter where the run wrote one. `Runner.served` holds those paths, and
`/file` refuses anything else.

Every endpoint answers a failure with the exception's message, under the same
`error` key as a refusal. See `_answering`.
"""

import functools
import json
import subprocess
import threading
import traceback
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional
from urllib.parse import quote

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from in2lambda_agent import corpus, pair, pipeline, routes
from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.model import ModelError, ModelUnavailable, choose_backend
from in2lambda_agent.settings import Settings, load_settings

PAGE = Path(__file__).parent / "page.html"

SUFFIXES = ("tex", "md", "docx", "pdf")
"""The source files the picker lists."""

DEFAULT_PORT = 8765

LAST = ("done", "error")
"""The events that end a stream: the run has ended. Every other event is
followed by another on the same stream."""

POLL_SECONDS = 0.05
"""How often the event stream looks for events the run thread has added. The
run thread cannot wake the event loop, and a sleep this short is a stage line
in the browser as soon as the stage finishes."""

FAILURES = (MathpixError, ModelUnavailable, ModelError)
"""The failures the `convert` command prints as one line, which the page shows
the same way. Any other exception reaches the page as a traceback: the page is
a development harness, and the developer reads the traceback."""


class RunBusy(RuntimeError):
    """A run is already going, and this server runs one at a time."""


def _answering(endpoint: Callable) -> Callable:
    """Wraps one endpoint so that a failure reaches the page as one line.

    A corpus holds files the process may not read, and the page can post a
    field it has not filled in. Starlette answers an exception with a 500 whose
    body is no JSON, which the page cannot read, and prints the traceback to
    the terminal. This answers with the exception's message under the `error`
    key that every refusal here uses, which the page shows as a line of the log.

    Args:
        endpoint: The endpoint to wrap.

    Returns:
        The same endpoint, answering a failure with the message.
    """

    @functools.wraps(endpoint)
    async def caught(request: Request) -> Response:
        try:
            return await endpoint(request)
        except Exception as error:
            return JSONResponse(
                {"error": f"{type(error).__name__}: {error}"}, status_code=500
            )

    return caught


@dataclass
class Options:
    """What the page asked a run to do, read from the POST body."""

    source: Path
    out_dir: Path
    solutions: Optional[Path] = None
    filter: Optional[Path] = None
    write_filter: bool = False


class Runner:
    """The one run the server has, and the events the page reads from it."""

    def __init__(self, settings: Settings, cache_dir: Path):
        self.settings = settings
        self.cache_dir = Path(cache_dir)
        self.state = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.served: set[Path] = set()
        self.thread: Optional[threading.Thread] = None
        self.options: Optional[Options] = None

    @property
    def busy(self) -> bool:
        """Whether the run thread is still going."""
        return self.thread is not None and self.thread.is_alive()

    def start(self, options: Options) -> None:
        """Starts a run, and forgets the events of the run before it.

        Args:
            options: What the page asked the run to do.

        Raises:
            RunBusy: a run is already going.
        """
        with self.state:
            if self.busy:
                raise RunBusy("a run is already going: wait for it to finish")
            self.events = []
            self.served = set()
            self.options = options
        self._start(self._run, options)

    def since(self, index: int) -> list[dict[str, Any]]:
        """The events from `index` onwards, which is none while a stage runs.

        Args:
            index: How many events the caller has already read.

        Returns:
            The events after those, oldest first.
        """
        with self.state:
            return self.events[index:]

    def _start(self, target: Any, *arguments: Any) -> None:
        """Runs one conversion in a thread of its own."""
        self.thread = threading.Thread(target=target, args=arguments, daemon=True)
        self.thread.start()

    def _run(self, options: Options) -> None:
        """The run thread: one call to `routes.convert`, and then its result."""
        try:
            solutions = options.solutions or pair.solutions_beside(options.source)
            backend = choose_backend(self.settings)
            lua = options.filter
            if options.write_filter:
                options.out_dir.mkdir(parents=True, exist_ok=True)
                lua = options.out_dir / "filter.lua"
                lua.write_text(
                    routes.write_filter(options.source, solutions, backend)[0],
                    encoding="utf-8",
                )
                self._stage("filter", str(lua))
            result = routes.convert(
                options.source,
                solutions,
                out_dir=options.out_dir,
                cache_dir=self.cache_dir,
                backend=backend,
                settings=self.settings,
                lua=lua,
                name=options.source.stem,
                on_stage=self._stage,
            )
        except FAILURES as error:
            self._emit({"type": "error", "message": str(error)})
        except subprocess.CalledProcessError as error:
            # pandoc read the document, or ran the filter, and refused. Its own
            # message names the line; the exit status alone names nothing.
            stderr = (error.stderr or b"").decode("utf-8", "replace").strip()
            self._emit({"type": "error", "message": stderr or str(error)})
        except Exception:
            self._emit({"type": "error", "message": traceback.format_exc()})
        else:
            self._finished(result)

    def _stage(self, name: str, message: str) -> None:
        """One stage line, on its way to the page."""
        self._emit({"type": "stage", "name": name, "message": message})

    def _finished(self, result: routes.Converted) -> None:
        """The last event of a run: the flags a person reads, and the links."""
        self._emit(
            {
                "type": "done",
                "flags": [
                    {"field": one.field, "a": one.a, "b": one.b, "reason": one.reason}
                    for one in result.flags
                ],
                "fields": result.counted(),
                "route_b_error": result.route_b_error,
                "tokens": result.tokens,
                "links": self._links(result),
            }
        )

    def _links(self, result: routes.Converted) -> list[dict[str, str]]:
        """What the run wrote, as links the page shows when the run has ended."""
        found = [("zip", result.zip_path)]
        if self.options is not None and self.options.write_filter:
            found.append(("filter.lua", self.options.out_dir / "filter.lua"))
        links = []
        for label, path in found:
            url = self._url(path)
            if url is not None:
                links.append({"label": label, "url": url})
        return links

    def _url(self, path: Optional[Any]) -> Optional[str]:
        """The `/file` URL for one path, and permission for the page to read it.

        Args:
            path: A file the run wrote, or None where the run wrote none.

        Returns:
            The URL, or None where there is no such file.
        """
        if path is None:
            return None
        resolved = Path(path).resolve()
        if not resolved.is_file():
            return None
        self.served.add(resolved)
        # A corpus folder can be named `Problem Sheet #3`, and a browser cuts a
        # URL at the `#`, reads a `&` as the next parameter and a `+` as a
        # space. So the path is escaped here and `/file` reads it back.
        return f"/file?path={quote(str(resolved))}"

    def _emit(self, event: dict[str, Any]) -> None:
        """Adds one event to the run's list, where the open streams read it."""
        with self.state:
            self.events.append(event)


def _entry(path: Path) -> dict[str, str]:
    """One folder or document of a listing, as the picker shows it."""
    return {"name": path.name, "path": str(path)}


def default_corpus() -> Path:
    """Where the picker looks when `--corpus` names nothing.

    Returns:
        `ExampleContents` under the directory the server was started in, where
        that is a directory, and that directory itself where it is not.
    """
    example = Path("ExampleContents")
    return example if example.is_dir() else Path(".")


def build_app(
    corpus_dir: Optional[Path] = None,
    *,
    settings: Optional[Settings] = None,
    cache_dir: Path = pipeline.DEFAULT_CACHE_DIR,
) -> Starlette:
    """The page and its endpoints, over one runner.

    Args:
        corpus_dir: The directory the picker lists, `default_corpus()` where
            nothing is named.
        settings: The environment a run has available, read from the process's
            own where nothing is given.
        cache_dir: Where the OCR of each PDF is kept.

    Returns:
        The application `serve` runs, and the tests drive with a test client.
    """
    root = Path(corpus_dir or default_corpus()).resolve()
    runner = Runner(settings or load_settings(), Path(cache_dir).resolve())

    @_answering
    async def page(request: Request) -> Response:
        return FileResponse(PAGE, media_type="text/html")

    @_answering
    async def sources(request: Request) -> Response:
        """One directory of the corpus: the folders in it, and its documents.

        The picker starts at the corpus and descends a directory at a time, so
        a corpus of thousands of files is never walked or listed at once.
        """
        where = Path(request.query_params.get("path") or root).resolve()
        if where != root and root not in where.parents:
            # The picker descends from the corpus and climbs no higher than it.
            # A source elsewhere is typed into the box beside the picker.
            where = root
        folders, documents = [], []
        for path in sorted(where.iterdir(), key=lambda one: one.name.lower()):
            if path.is_dir():
                folders.append(path)
            elif path.suffix.lower().lstrip(".") in SUFFIXES and corpus.is_document(
                path
            ):
                documents.append(path)
        return JSONResponse(
            {
                "root": str(root),
                "path": str(where),
                "up": None if where == root else str(where.parent),
                "folders": [_entry(path) for path in folders],
                "documents": [_entry(path) for path in documents],
            }
        )

    @_answering
    async def run(request: Request) -> Response:
        body = await request.json()
        source = Path(body.get("source") or "")
        if not source.is_file():
            return JSONResponse({"error": f"{source} is not a file"}, status_code=400)
        options = Options(
            source=source,
            out_dir=Path(body.get("out") or "out"),
            solutions=Path(body["solutions"]) if body.get("solutions") else None,
            filter=Path(body["filter"]) if body.get("filter") else None,
            write_filter=bool(body.get("write_filter")),
        )
        try:
            runner.start(options)
        except RunBusy as busy:
            return JSONResponse({"error": str(busy)}, status_code=409)
        return JSONResponse({"started": str(source)})

    @_answering
    async def events(request: Request) -> Response:
        index = int(request.query_params.get("since", 0))

        async def lines() -> AsyncIterator[str]:
            nonlocal index
            while True:
                found = runner.since(index)
                index += len(found)
                for event in found:
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] in LAST:
                        return
                if await request.is_disconnected():
                    return
                await anyio.sleep(POLL_SECONDS)

        return StreamingResponse(lines(), media_type="text/event-stream")

    @_answering
    async def file(request: Request) -> Response:
        path = Path(request.query_params.get("path", "")).resolve()
        # A link the page rendered outlives the file behind it: a later run
        # writes over the out directory, and the zip of the run before it is
        # gone. That is a 404 like any other, not a traceback.
        if path not in runner.served or not path.is_file():
            return JSONResponse(
                {"error": f"{path} is not a file this run wrote"}, status_code=404
            )
        return FileResponse(path)

    return Starlette(
        routes=[
            Route("/", page),
            Route("/api/sources", sources),
            Route("/api/run", run, methods=["POST"]),
            Route("/api/events", events),
            Route("/file", file),
        ]
    )


def serve(
    corpus_dir: Optional[Path] = None,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """Serves the page on 127.0.0.1 until the command is interrupted.

    Args:
        corpus_dir: The directory the picker lists.
        port: The port to listen on.
        open_browser: Open the page in the machine's browser once the server
            has started.
    """
    import uvicorn

    url = f"http://127.0.0.1:{port}/"
    print(f"in2lambda-agent ui: {url}")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    uvicorn.run(build_app(corpus_dir), host="127.0.0.1", port=port, log_level="warning")
