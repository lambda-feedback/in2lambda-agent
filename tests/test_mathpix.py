"""The Mathpix client, with every request faked at the transport.

No test here touches the network or needs a credential.
"""

import httpx
import pytest

from in2lambda_agent.mathpix import MathpixClient, MathpixError, MissingCredentials
from in2lambda_agent.settings import Settings

MARKDOWN = (
    "# Tutorial Sheet 4\n\n"
    "![a plot](https://cdn.mathpix.com/plot.png?token=abc)\n\n"
    "Question 1.\n"
)


def completed(conversion="completed"):
    """The poll response Mathpix gives once it has finished."""
    return {"status": "completed", "conversion_status": {"md": {"status": conversion}}}


def mathpix(markdown=MARKDOWN, polls=(completed(),), image=b"PNG"):
    """A handler for the three calls, answering the polls in turn."""
    remaining = list(polls)

    def handler(request):
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"pdf_id": "abc"})
        if url.endswith("/v3/pdf/abc"):
            return httpx.Response(200, json=remaining.pop(0) if remaining else {})
        if url.endswith("/v3/pdf/abc.md"):
            return httpx.Response(200, text=markdown)
        return httpx.Response(200, content=image)

    return handler


def build(handler, **kwargs):
    """A client over a handler, with the requests it makes recorded."""
    calls = []
    now = [0.0]

    def record(request):
        calls.append(request)
        return handler(request)

    client = MathpixClient(
        "app",
        "key",
        transport=httpx.MockTransport(record),
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock=lambda: now[0],
        **kwargs,
    )
    return client, calls


def test_a_conversion_returns_the_markdown_and_writes_its_images(pdf, tmp_path):
    client, calls = build(mathpix())

    markdown = client.convert(pdf, tmp_path / "media")

    # The reference is the bare basename, with the URL's query stripped, so the
    # folder can be dropped into the set's media folder as it stands.
    assert "![a plot](plot.png)" in markdown
    assert (tmp_path / "media" / "plot.png").read_bytes() == b"PNG"
    assert [call.method for call in calls] == ["POST", "GET", "GET", "GET"]


def test_the_upload_asks_for_markdown(pdf, tmp_path):
    client, calls = build(mathpix())

    client.convert(pdf, tmp_path / "media")

    assert b'"conversion_formats"' in calls[0].content
    assert calls[0].headers["app_id"] == "app"


def test_polling_carries_on_until_the_markdown_is_ready(pdf, tmp_path):
    handler = mathpix(polls=({"status": "split"}, {"status": "processing"}, completed()))
    client, calls = build(handler)

    client.convert(pdf, tmp_path / "media")

    assert [call.method for call in calls].count("GET") == 5


def test_two_images_with_the_same_name_do_not_overwrite_each_other(pdf, tmp_path):
    markdown = (
        "![one](https://cdn.mathpix.com/a/plot.png)\n"
        "![two](https://cdn.mathpix.com/b/plot.png)\n"
        "![one again](https://cdn.mathpix.com/a/plot.png)\n"
    )
    client, _ = build(mathpix(markdown=markdown))

    converted = client.convert(pdf, tmp_path / "media")

    assert "![one](plot.png)" in converted
    assert "![two](plot-2.png)" in converted
    assert "![one again](plot.png)" in converted
    assert sorted(path.name for path in (tmp_path / "media").iterdir()) == [
        "plot-2.png",
        "plot.png",
    ]


def test_rejected_credentials_say_which_variables_to_check(pdf, tmp_path):
    client, _ = build(lambda request: httpx.Response(401, text="unauthorized"))

    with pytest.raises(MathpixError) as error:
        client.convert(pdf, tmp_path / "media")

    assert "MATHPIX_APP_ID" in str(error.value)
    assert "MATHPIX_API_KEY" in str(error.value)


def test_an_error_status_carries_mathpixs_own_message(pdf, tmp_path):
    # The live body says why in error_info.message; `error` beside it is only a
    # code, and reporting that one leaves the reader none the wiser.
    poll = {
        "status": "error",
        "error": "pdf_page_limit_exceeded",
        "error_info": {
            "id": "pdf_page_limit_exceeded",
            "message": "page 3 is not a page",
        },
    }
    client, _ = build(mathpix(polls=(poll,)))

    with pytest.raises(MathpixError, match="page 3 is not a page"):
        client.convert(pdf, tmp_path / "media")


def test_a_redirected_poll_is_an_error_not_a_decoding_crash(pdf, tmp_path):
    def handler(request):
        if str(request.url).endswith("/v3/pdf/abc"):
            return httpx.Response(302, headers={"location": "https://elsewhere/"})
        return mathpix()(request)

    client, _ = build(handler)

    with pytest.raises(MathpixError, match="returned 302 while polling"):
        client.convert(pdf, tmp_path / "media")


def test_a_redirected_image_is_an_error_not_an_empty_file(pdf, tmp_path):
    def handler(request):
        if request.url.host == "cdn.mathpix.com":
            return httpx.Response(301, headers={"location": "https://elsewhere/p.png"})
        return mathpix()(request)

    client, _ = build(handler)

    with pytest.raises(MathpixError, match="returned 301 while fetching image plot.png"):
        client.convert(pdf, tmp_path / "media")

    assert not (tmp_path / "media").exists()


def test_a_failed_markdown_conversion_is_an_error_not_a_poll(pdf, tmp_path):
    poll = {
        "status": "completed",
        "conversion_status": {
            "md": {"status": "error", "error_info": {"message": "no text found"}}
        },
    }
    client, _ = build(mathpix(polls=(poll,)))

    with pytest.raises(MathpixError, match="no text found"):
        client.convert(pdf, tmp_path / "media")


def test_a_request_that_times_out_names_the_step_it_was_in(pdf, tmp_path):
    def handler(request):
        if str(request.url).endswith(".md"):
            raise httpx.ReadTimeout("too slow", request=request)
        return mathpix()(request)

    client, _ = build(handler)

    with pytest.raises(MathpixError, match="timed out fetching markdown"):
        client.convert(pdf, tmp_path / "media")


def test_polling_gives_up_at_the_deadline(pdf, tmp_path):
    client, calls = build(
        mathpix(polls=()), poll_interval=2.0, deadline=10.0
    )

    with pytest.raises(MathpixError, match="did not finish abc within 10s"):
        client.convert(pdf, tmp_path / "media")

    # The deadline bounds the polling rather than the run going on for ever.
    assert [call.method for call in calls].count("GET") == 6


def test_from_settings_names_only_the_variables_that_are_unset():
    with pytest.raises(MissingCredentials) as error:
        MathpixClient.from_settings(Settings(mathpix_app_id="app"))

    assert error.value.variables == ["MATHPIX_API_KEY"]
    assert "MATHPIX_APP_ID" not in str(error.value)
    assert ".env" in str(error.value)


def test_from_settings_names_both_when_neither_is_set():
    with pytest.raises(MissingCredentials) as error:
        MathpixClient.from_settings(Settings())

    assert error.value.variables == ["MATHPIX_APP_ID", "MATHPIX_API_KEY"]


def test_from_settings_builds_a_client_when_both_are_set():
    settings = Settings(mathpix_app_id="app", mathpix_api_key="key")

    assert isinstance(MathpixClient.from_settings(settings), MathpixClient)
