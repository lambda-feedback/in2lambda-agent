"""The local page's endpoints, over a faked pipeline.

No test here calls a model, starts a server on a port, or opens a browser: the
test client drives the application in this process, and `pipeline.run` and
`pipeline.resume` are replaced by functions that report the stages a run would
have reported.
"""

import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("starlette", reason="the ui extra is not installed")

import anyio  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from in2lambda_agent import pipeline  # noqa: E402
from in2lambda_agent.model import ModelUnavailable  # noqa: E402
from in2lambda_agent.review import Question, Review  # noqa: E402
from in2lambda_agent.settings import Settings  # noqa: E402
from in2lambda_agent.spec import RECORD_NAME, SPEC_NAME  # noqa: E402
from in2lambda_agent.ui import server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def root(tmp_path):
    """A corpus of one sheet, one PDF and one tex fragment, with a spec beside."""
    folder = tmp_path / "corpus"
    (folder / "figures").mkdir(parents=True)
    (folder / "sheet.md").write_text((FIXTURES / "sheet.md").read_text())
    (folder / "sheet.pdf").write_bytes(b"%PDF-1.4 not really a PDF")
    (folder / SPEC_NAME).write_text((FIXTURES / "sheet-spec.yaml").read_text())
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


def waiting_review(root, tmp_path, status="pending"):
    """A review of one question with a PDF, as a stopped run leaves one."""
    pdf = written(tmp_path / "out" / "render", "q1.pdf", "a PDF")
    return Review(
        mode="per-question",
        count=1,
        source=str(root / "sheet.md"),
        spec=str(root / SPEC_NAME),
        out_dir=str(tmp_path / "out"),
        limit=3,
        draft=str(tmp_path / "draft.json"),
        frozen=str(root / "sheet.md"),
        reused=True,
        coverage=None,
        questions=[Question(key="q1", pdf=str(pdf), lines=[[3, 5]], status=status)],
    )


def faked(monkeypatch, name, stages=(), **fields):
    """Replaces one pipeline call with one that reports `stages` and returns.

    Args:
        monkeypatch: The fixture that puts the real call back afterwards.
        name: `run` or `resume`.
        stages: The stage lines the call reports through `on_stage`.
        fields: What the RunResult it returns carries.

    Returns:
        A list the call appends its keyword arguments to.
    """
    seen = []

    def call(*positional, on_stage=None, **given):
        seen.append({"positional": positional, **given})
        result = pipeline.RunResult(on_stage=on_stage, **fields)
        for stage, message in stages:
            result.add_stage(stage, message)
        return result

    monkeypatch.setattr(pipeline, name, call)
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
    # The sheet and the PDF, and not the spec file beside them.
    assert [one["name"] for one in answer["documents"]] == ["sheet.md", "sheet.pdf"]
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

    monkeypatch.setattr(server.corpus, "is_document", refuse)

    answer = client.get(f"/api/sources?path={root / 'figures'}")

    assert answer.status_code == 500
    assert answer.json() == {
        "error": f"PermissionError: [Errno 13] Permission denied: '{unreadable}'"
    }


def test_a_field_the_page_did_not_fill_in_is_one_line(client, root):
    answer = client.post(
        "/api/run", json={"source": str(root / "sheet.md"), "rounds": None}
    )

    assert answer.status_code == 500
    assert answer.json()["error"].startswith("TypeError: ")


def test_a_run_streams_its_stages_and_then_its_links(
    client, root, tmp_path, monkeypatch
):
    zip_path = written(tmp_path / "out", "set.zip")
    (root / RECORD_NAME).write_text("{}\n")
    seen = faked(
        monkeypatch,
        "run",
        stages=[("ocr", "not needed for sheet.md"), ("build", str(zip_path))],
        zip_path=zip_path,
        draft=tmp_path / "draft.json",
        reason="",
    )
    written(tmp_path, "draft.json", "{}")

    started = client.post(
        "/api/run",
        json={
            "source": str(root / "sheet.md"),
            "out": str(tmp_path / "out"),
            "review": "none",
            "rounds": 2,
            "sample": 1,
            "fresh_ocr": True,
        },
    )
    found = events(client)

    assert started.status_code == 200
    assert seen[0]["positional"] == (root / "sheet.md",)
    assert (seen[0]["rounds"], seen[0]["sample"], seen[0]["fresh_ocr"]) == (2, 1, True)
    assert [one["name"] for one in found if one["type"] == "stage"] == ["ocr", "build"]
    links = {one["label"]: one["url"] for one in found[-1]["links"]}
    assert set(links) == {"zip", "draft", "spec", "runs"}
    assert client.get(links["zip"]).text == "a zip"


def test_a_stage_reaches_the_page_before_the_run_ends(client, app, root, monkeypatch):
    held = threading.Event()
    ended = threading.Event()

    def call(*positional, on_stage=None, **given):
        result = pipeline.RunResult(on_stage=on_stage)
        result.add_stage("ocr", "not needed for sheet.md")
        held.wait(timeout=10)
        result.add_stage("build", "set.zip")
        ended.set()
        return result

    monkeypatch.setattr(pipeline, "run", call)
    client.post("/api/run", json={"source": str(root / "sheet.md")})

    first = first_event(app)
    # The run is still in its first stage, so the ocr line was sent as that
    # stage finished and not with the rest of them at the end.
    still_going = not ended.is_set()
    held.set()

    assert first == {
        "type": "stage",
        "name": "ocr",
        "message": "not needed for sheet.md",
    }
    assert still_going


def test_a_link_escapes_the_path_it_carries(client, root, tmp_path, monkeypatch):
    # A corpus folder can be named anything, and `#`, `&` and `+` all mean
    # something else in a URL.
    zip_path = written(tmp_path / "Problem Sheet #3 & 4+", "set.zip")
    faked(monkeypatch, "run", zip_path=zip_path)

    client.post("/api/run", json={"source": str(root / "sheet.md")})
    found = events(client)
    links = {one["label"]: one["url"] for one in found[-1]["links"]}

    assert "%23" in links["zip"]
    assert client.get(links["zip"]).text == "a zip"


def test_a_second_run_while_one_is_going_is_refused(client, root, monkeypatch):
    held = threading.Event()

    def call(*positional, on_stage=None, **given):
        held.wait(timeout=10)
        return pipeline.RunResult()

    monkeypatch.setattr(pipeline, "run", call)
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

    monkeypatch.setattr(pipeline, "run", call)

    client.post("/api/run", json={"source": str(root / "sheet.md")})
    found = events(client)

    assert found[-1] == {"type": "error", "message": "set ANTHROPIC_API_KEY"}


def test_a_run_that_stops_for_review_sends_its_questions_and_their_pdfs(
    client, root, tmp_path, monkeypatch
):
    faked(
        monkeypatch,
        "run",
        stages=[("review", "mode per-question, 1 of 1 questions waiting")],
        review=waiting_review(root, tmp_path),
    )

    client.post(
        "/api/run",
        json={"source": str(root / "sheet.md"), "review": "per-question"},
    )
    found = events(client)
    question = found[-1]["questions"][0]

    assert found[-1]["mode"] == "per-question"
    assert (question["key"], question["status"]) == ("q1", "pending")
    assert question["lines"] == [[3, 5]]
    assert client.get(question["pdf"]).status_code == 200


def test_a_rejection_is_answered_and_its_rounds_stream_on(
    client, root, tmp_path, monkeypatch
):
    faked(monkeypatch, "run", review=waiting_review(root, tmp_path))
    client.post(
        "/api/run",
        json={"source": str(root / "sheet.md"), "review": "per-question"},
    )
    stopped = events(client)
    zip_path = written(tmp_path / "out", "set.zip")
    seen = faked(
        monkeypatch,
        "resume",
        stages=[("fix", "round 1: field replace, 900 tokens, 2.0s")],
        review=waiting_review(root, tmp_path, status="approved"),
        zip_path=zip_path,
    )

    answered = client.post(
        "/api/review",
        json={"verdict": "reject", "key": "q1", "note": "part (b) is the solution"},
    )
    # The page reads on from the review it answered, which is where the stream
    # that carried the review ended.
    found = events(client, since=len(stopped))

    assert answered.status_code == 200
    assert [one["type"] for one in stopped] == ["review"]
    assert (seen[0]["verdict"], seen[0]["key"]) == ("reject", "q1")
    assert seen[0]["note"] == "part (b) is the solution"
    assert [one["name"] for one in found if one["type"] == "stage"] == ["fix"]
    assert "zip" in {one["label"] for one in found[-1]["links"]}


def test_an_edit_carries_the_field_and_the_reviewer(
    client, root, tmp_path, monkeypatch
):
    seen = faked(monkeypatch, "resume", review=waiting_review(root, tmp_path))

    answered = client.post(
        "/api/review",
        json={
            "verdict": "edit",
            "field": "q1.text",
            "old": r"\mathrm{m/s$",
            "new": r"\mathrm{m/s}$",
            "by": "peter",
        },
    )
    events(client)

    assert answered.status_code == 200
    assert seen[0]["field"] == "q1.text"
    assert (seen[0]["old"], seen[0]["new"]) == (r"\mathrm{m/s$", r"\mathrm{m/s}$")
    assert seen[0]["by"] == "peter"


def test_a_verdict_that_is_not_one_of_the_three_is_refused(client):
    answer = client.post("/api/review", json={"verdict": "maybe", "key": "q1"})

    assert answer.status_code == 400
    assert "approve, reject or edit" in answer.json()["error"]


def test_a_file_the_run_did_not_write_is_not_served(client, root):
    answer = client.get(f"/file?path={root / 'sheet.md'}")

    assert answer.status_code == 404
    assert "is not a file this run wrote" in answer.json()["error"]


def test_a_link_to_a_file_that_has_gone_is_not_served(
    client, root, tmp_path, monkeypatch
):
    zip_path = written(tmp_path / "out", "set.zip")
    faked(monkeypatch, "run", zip_path=zip_path)
    client.post("/api/run", json={"source": str(root / "sheet.md")})
    links = {one["label"]: one["url"] for one in events(client)[-1]["links"]}
    zip_path.unlink()

    answer = client.get(links["zip"])

    assert answer.status_code == 404
    assert "is not a file this run wrote" in answer.json()["error"]
