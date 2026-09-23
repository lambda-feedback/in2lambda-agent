"""The local page's endpoints, over a faked conversion.

No test here calls a model, starts a server on a port, or opens a browser,
except the live one at the end: the test client drives the application in this
process, and `routes.convert` is replaced by a function that reports the stages
a run would have reported.
"""

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

pytest.importorskip("starlette", reason="the ui extra is not installed")

import anyio  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from in2lambda_agent import routes  # noqa: E402
from in2lambda_agent.model import ModelUnavailable  # noqa: E402
from in2lambda_agent.settings import Settings, load_settings  # noqa: E402
from in2lambda_agent.ui import server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

ME2_TARGET = Path(
    "/Users/peterbjohnson/code/lambdafeedback/in2lambda-agent/ExampleContents/targets/"
    "ME2_Fluids_introduction"
)

live = pytest.mark.skipif(
    not os.environ.get("IN2LAMBDA_LIVE") or not ME2_TARGET.is_dir(),
    reason="calls Mathpix and a model over the private corpus",
)


@pytest.fixture
def root(tmp_path):
    """A corpus of one sheet, its solutions, a PDF and one tex fragment."""
    folder = tmp_path / "corpus"
    (folder / "figures").mkdir(parents=True)
    (folder / "sheet.md").write_text((FIXTURES / "sheet.md").read_text())
    (folder / "sheet_solutions.md").write_text((FIXTURES / "sheet-2.md").read_text())
    (folder / "sheet.pdf").write_bytes(b"%PDF-1.4 not really a PDF")
    # A tex file with no \begin{document}: input to a document, not one itself.
    (folder / "figures" / "ball.tex").write_text(r"\draw (0,0) circle (1);")
    return folder


@pytest.fixture
def app(root, tmp_path):
    """The application over that corpus, with its cache under tmp."""
    return server.build_app(root, settings=Settings(), cache_dir=tmp_path / "cache")


@pytest.fixture
def client(app):
    """A client that drives the application in this process."""
    with TestClient(app) as client:
        yield client


def written(tmp_path, name="set.zip", text="a zip"):
    """A file a faked run claims to have written."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def faked(monkeypatch, stages=(), **fields):
    """Replaces `routes.convert` with one that reports `stages` and returns.

    Args:
        monkeypatch: The fixture that puts the real call back afterwards.
        stages: The stage lines the call reports through `on_stage`.
        fields: What the `Converted` it returns carries.

    Returns:
        A list the call appends its arguments to.
    """
    seen = []

    def call(*positional, on_stage=None, **given):
        seen.append({"positional": positional, **given})
        for name, message in stages:
            on_stage(name, message)
        return routes.Converted(
            **{"set": None, "zip_path": None, "flags": [], "reply": [], **fields}
        )

    monkeypatch.setattr(server.routes, "convert", call)
    return seen


def events(client, since=0):
    """Every event of one stream, which ends once the run is idle."""
    answer = client.get(f"/api/events?since={since}")
    return [
        json.loads(line[len("data: ") :])
        for line in answer.text.splitlines()
        if line.startswith("data: ")
    ]


class Stopped(Exception):
    """Ends a stream at its first chunk, as a browser that closes one does."""


def first_event(app):
    """The first event of a stream, read while the run is still going.

    The test client runs the whole application before it hands back a response,
    so a stream it reads says nothing about when each chunk was sent. This
    drives the application itself and stops at the first chunk, which arrives
    only once the run thread has reported a stage.

    Args:
        app: The application to open the stream on.

    Returns:
        The event that chunk carries.
    """
    sent = []

    async def receive():
        # The browser holds the connection open and sends nothing more, and
        # Starlette waits on this for the disconnect that ends the stream.
        await anyio.sleep_forever()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent.append(message["body"].decode())
            raise Stopped

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/events",
        "root_path": "",
        "scheme": "http",
        "query_string": b"since=0",
        "headers": [],
        "client": ("127.0.0.1", 0),
    }

    async def drive():
        with anyio.fail_after(10):
            await app(scope, receive, send)

    try:
        anyio.run(drive)
    except BaseException:
        # `Stopped`, as the task group running the stream re-raises it.
        pass
    assert sent, "the stream sent nothing within ten seconds"
    return json.loads(sent[0][len("data: ") :])


def test_the_page_is_served(client):
    answer = client.get("/")

    assert answer.status_code == 200
    assert "<title>in2lambda agent</title>" in answer.text


def test_the_picker_lists_one_directory_of_the_corpus(client, root):
    answer = client.get("/api/sources").json()

    assert (answer["root"], answer["path"]) == (str(root), str(root))
    assert answer["up"] is None
    assert [one["name"] for one in answer["folders"]] == ["figures"]
    assert [one["name"] for one in answer["documents"]] == [
        "sheet.md",
        "sheet.pdf",
        "sheet_solutions.md",
    ]
    assert answer["documents"][0]["path"] == str(root / "sheet.md")


def test_the_picker_descends_into_a_folder(client, root):
    answer = client.get(f"/api/sources?path={root / 'figures'}").json()

    assert answer["path"] == str(root / "figures")
    assert answer["up"] == str(root)
    # A tex file with no \begin{document} is input to a document, not one.
    assert answer["documents"] == []


def test_the_picker_climbs_no_higher_than_the_corpus(client, root, tmp_path):
    answer = client.get(f"/api/sources?path={tmp_path}").json()

    assert answer["path"] == str(root)


def test_a_file_the_process_may_not_read_is_one_line(client, root, monkeypatch):
    unreadable = root / "figures" / "ball.tex"

    def refuse(path):
        raise PermissionError(f"[Errno 13] Permission denied: '{path}'")

    monkeypatch.setattr(server.pair, "is_document", refuse)

    answer = client.get(f"/api/sources?path={root / 'figures'}")

    assert answer.status_code == 500
    assert answer.json() == {
        "error": f"PermissionError: [Errno 13] Permission denied: '{unreadable}'"
    }


def test_a_field_the_page_did_not_fill_in_is_one_line(client, root):
    answer = client.post("/api/run", json={"source": str(root / "sheet.md"), "out": 3})

    assert answer.status_code == 500
    assert answer.json()["error"].startswith("TypeError: ")


def test_a_run_streams_its_stages_and_then_its_flags(
    client, root, tmp_path, monkeypatch
):
    zip_path = written(tmp_path / "out", "set.zip")
    faked(
        monkeypatch,
        stages=[
            ("ocr", "sheet.md: read"),
            ("route A", "1200 tokens"),
            ("route B", "did not run: no filter"),
            ("fields", "10 fields, agreed 9, defaulted 0, adjudicated 0, flagged 1"),
            ("build", str(zip_path)),
        ],
        zip_path=zip_path,
        flags=[routes.Flag("q2.p2.content", "Find the drag.", "Find the drag, in N.", "two readings")],
        fields=10,
        agreed=9,
        tokens=1200,
    )

    started = client.post(
        "/api/run",
        json={"source": str(root / "sheet.md"), "out": str(tmp_path / "out")},
    )
    found = events(client)

    assert started.status_code == 200
    assert [one["name"] for one in found if one["type"] == "stage"] == [
        "ocr",
        "route A",
        "route B",
        "fields",
        "build",
    ]
    done = found[-1]
    assert done["type"] == "done"
    assert done["flags"] == [
        {
            "field": "q2.p2.content",
            "a": "Find the drag.",
            "b": "Find the drag, in N.",
            "reason": "two readings",
        }
    ]
    assert done["fields"] == "10 fields, agreed 9, defaulted 0, adjudicated 0, flagged 1"
    assert (done["tokens"], done["route_b_error"]) == (1200, None)
    links = {one["label"]: one["url"] for one in done["links"]}
    assert set(links) == {"zip"}
    assert client.get(links["zip"]).text == "a zip"


def test_the_solutions_beside_the_source_are_converted_with_it(
    client, root, monkeypatch
):
    seen = faked(monkeypatch)

    client.post("/api/run", json={"source": str(root / "sheet.md")})
    events(client)

    assert seen[0]["positional"] == (
        root / "sheet.md",
        root / "sheet_solutions.md",
    )


def test_a_named_solutions_file_is_taken_over_the_one_beside(
    client, root, tmp_path, monkeypatch
):
    named = written(tmp_path, "elsewhere.md", "# Solutions\n")
    seen = faked(monkeypatch)

    client.post(
        "/api/run",
        json={"source": str(root / "sheet.md"), "solutions": str(named)},
    )
    events(client)

    assert seen[0]["positional"] == (root / "sheet.md", named)


def test_a_filter_file_reaches_the_conversion(client, root, tmp_path, monkeypatch):
    lua = written(tmp_path, "set.lua", "function Pandoc(doc) end\n")
    seen = faked(monkeypatch)

    client.post(
        "/api/run", json={"source": str(root / "sheet.md"), "filter": str(lua)}
    )
    events(client)

    assert seen[0]["lua"] == lua


def test_the_page_writes_a_filter_and_links_it(client, root, tmp_path, monkeypatch):
    monkeypatch.setattr(
        server.routes,
        "write_filter",
        lambda document, solutions, backend, **_: ("function Pandoc(doc) end\n", None),
    )
    seen = faked(monkeypatch, zip_path=written(tmp_path / "out", "set.zip"))

    client.post(
        "/api/run",
        json={
            "source": str(root / "sheet.md"),
            "out": str(tmp_path / "out"),
            "write_filter": True,
        },
    )
    found = events(client)
    links = {one["label"]: one["url"] for one in found[-1]["links"]}

    lua = tmp_path / "out" / "filter.lua"
    assert lua.read_text() == "function Pandoc(doc) end\n"
    assert seen[0]["lua"] == lua
    assert [one["name"] for one in found if one["type"] == "stage"] == ["filter"]
    assert client.get(links["filter.lua"]).text == "function Pandoc(doc) end\n"


def test_a_stage_reaches_the_page_before_the_run_ends(client, app, root, monkeypatch):
    held = threading.Event()
    ended = threading.Event()

    def call(*positional, on_stage=None, **given):
        on_stage("ocr", "sheet.md: read")
        held.wait(timeout=10)
        on_stage("build", "set.zip")
        ended.set()
        return routes.Converted(set=None, zip_path=None, flags=[], reply=[])

    monkeypatch.setattr(server.routes, "convert", call)
    client.post("/api/run", json={"source": str(root / "sheet.md")})

    first = first_event(app)
    # The run is still in its first stage, so the ocr line was sent as that
    # stage finished and not with the rest of them at the end.
    still_going = not ended.is_set()
    held.set()

    assert first == {"type": "stage", "name": "ocr", "message": "sheet.md: read"}
    assert still_going


def test_a_link_escapes_the_path_it_carries(client, root, tmp_path, monkeypatch):
    # A corpus folder can be named anything, and `#`, `&` and `+` all mean
    # something else in a URL.
    zip_path = written(tmp_path / "Problem Sheet #3 & 4+", "set.zip")
    faked(monkeypatch, zip_path=zip_path)

    client.post("/api/run", json={"source": str(root / "sheet.md")})
    found = events(client)
    links = {one["label"]: one["url"] for one in found[-1]["links"]}

    assert "%23" in links["zip"]
    assert client.get(links["zip"]).text == "a zip"


def test_a_second_run_while_one_is_going_is_refused(client, root, monkeypatch):
    held = threading.Event()

    def call(*positional, on_stage=None, **given):
        held.wait(timeout=10)
        return routes.Converted(set=None, zip_path=None, flags=[], reply=[])

    monkeypatch.setattr(server.routes, "convert", call)
    body = {"source": str(root / "sheet.md")}

    first = client.post("/api/run", json=body)
    second = client.post("/api/run", json=body)
    held.set()

    assert first.status_code == 200
    assert second.status_code == 409
    assert "already going" in second.json()["error"]


def test_a_source_that_is_not_a_file_is_refused(client, root):
    answer = client.post("/api/run", json={"source": str(root / "nothing.md")})

    assert answer.status_code == 400
    assert "is not a file" in answer.json()["error"]


def test_a_failure_the_command_prints_reaches_the_page_as_one_line(
    client, root, monkeypatch
):
    def call(*positional, on_stage=None, **given):
        raise ModelUnavailable("set ANTHROPIC_API_KEY")

    monkeypatch.setattr(server.routes, "convert", call)

    client.post("/api/run", json={"source": str(root / "sheet.md")})
    found = events(client)

    assert found[-1] == {"type": "error", "message": "set ANTHROPIC_API_KEY"}


def test_a_document_pandoc_refuses_reaches_the_page_as_pandocs_own_message(
    client, root, monkeypatch
):
    # The exit status alone names nothing; pandoc's message names the line.
    def call(*positional, on_stage=None, **given):
        raise subprocess.CalledProcessError(
            43, ["pandoc"], stderr=b"Error at (line 4, column 1)\n"
        )

    monkeypatch.setattr(server.routes, "convert", call)

    client.post("/api/run", json={"source": str(root / "sheet.md")})
    found = events(client)

    assert found[-1] == {
        "type": "error",
        "message": "Error at (line 4, column 1)",
    }


def test_a_file_the_run_did_not_write_is_not_served(client, root):
    answer = client.get(f"/file?path={root / 'sheet.md'}")

    assert answer.status_code == 404
    assert "is not a file this run wrote" in answer.json()["error"]


def test_a_link_to_a_file_that_has_gone_is_not_served(
    client, root, tmp_path, monkeypatch
):
    zip_path = written(tmp_path / "out", "set.zip")
    faked(monkeypatch, zip_path=zip_path)
    client.post("/api/run", json={"source": str(root / "sheet.md")})
    links = {one["label"]: one["url"] for one in events(client)[-1]["links"]}
    zip_path.unlink()

    answer = client.get(links["zip"])

    assert answer.status_code == 404
    assert "is not a file this run wrote" in answer.json()["error"]


@live
def test_the_me2_pair_runs_from_the_page(tmp_path):
    # The ticket's run: the two PDFs posted from the page, the stages as they
    # happen, the flags a person reads and the zip.
    (pdf,) = [p for p in ME2_TARGET.glob("*.pdf") if "solutions" not in p.name]
    (solutions,) = ME2_TARGET.glob("*solutions.pdf")
    app = server.build_app(
        ME2_TARGET, settings=load_settings(), cache_dir=tmp_path / "cache"
    )

    with TestClient(app) as client:
        started = client.post(
            "/api/run",
            json={
                "source": str(pdf),
                "solutions": str(solutions),
                "out": str(tmp_path / "out"),
            },
        )
        found = events(client)
        print()
        for one in found:
            if one["type"] == "stage":
                print(f"{one['name']:<9} {one['message']}")
            elif one["type"] == "done":
                for flag in one["flags"]:
                    print(f"flag      {flag['field']}: {flag['reason']}")
                print(f"links     {[link['url'] for link in one['links']]}")
            else:
                print(f"error     {one['message']}")

        assert started.status_code == 200
        assert [one["name"] for one in found if one["type"] == "stage"] == [
            "ocr",
            "route A",
            "route B",
            "fields",
            "build",
        ]
        done = found[-1]
        # The printed solutions PDF holds separator lines that Mathpix reads as
        # minus signs, so the worked solutions of Friction on a plate and Towing
        # a submarine are flagged.
        assert [(one["field"], one["reason"]) for one in done["flags"]] == [
            ("q2.p1.worked_solution", routes.STRAY_MINUS),
            ("q3.p1.worked_solution", routes.STRAY_MINUS),
        ]
        links = {one["label"]: one["url"] for one in done["links"]}
        assert client.get(links["zip"]).status_code == 200
