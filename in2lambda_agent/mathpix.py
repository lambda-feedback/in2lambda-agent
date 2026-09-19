"""Mathpix OCR: a PDF in, markdown and the images it refers to out.

The parked in2lambda branch's wizard/mathpix.py did the same three calls; the
gaps the in2lambda board's t16 lists are closed here. Every request carries a
timeout, Mathpix's own error states are raised rather than polled over, and
polling gives up at a deadline instead of looping for ever.
"""

import json
import re
import time
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

import httpx

from in2lambda_agent.settings import Settings

# Mathpix also offers .mmd, its own markdown. Whether the pipeline wants that
# instead is one of the design spec's open questions; it is this constant.
MARKDOWN_FORMAT = "md"

# Mathpix returns images as absolute URLs on its CDN.
IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")


class MathpixError(Exception):
    """Anything that stopped a PDF becoming markdown."""


class MissingCredentials(MathpixError):
    """The run has no Mathpix credentials, and says which are unset."""

    def __init__(self, variables: Iterable[str]) -> None:
        self.variables = list(variables)
        super().__init__(
            f"set {' and '.join(self.variables)} (in the environment or .env) "
            "to convert a PDF"
        )


class MathpixClient:
    """The three Mathpix v3 PDF calls: upload, poll, fetch."""

    def __init__(
        self,
        app_id: str,
        app_key: str,
        *,
        base_url: str = "https://api.mathpix.com",
        timeout: float = 30.0,
        poll_interval: float = 2.0,
        deadline: float = 600.0,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Holds one HTTP client for the whole conversion.

        Args:
            app_id: MATHPIX_APP_ID.
            app_key: MATHPIX_API_KEY.
            base_url: The Mathpix API, overridden by the tests.
            timeout: Seconds any one request may take.
            poll_interval: Seconds between polls.
            deadline: Seconds a conversion may take before the run gives up.
            transport: An httpx transport, which is how the tests fake HTTP.
            sleep: Waits between polls; the tests pass their own.
            clock: Monotonic seconds; the tests pass their own.
        """
        self._base_url = base_url.rstrip("/")
        self._headers = {"app_id": app_id, "app_key": app_key}
        self._poll_interval = poll_interval
        self._deadline = deadline
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.Client(timeout=timeout, transport=transport)

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "MathpixClient":
        """Builds a client from the environment.

        Args:
            settings: The environment the run has available.
            **kwargs: Passed on to the constructor.

        Returns:
            A client.

        Raises:
            MissingCredentials: Naming exactly the variables that are unset.
        """
        missing = [
            name
            for name, value in (
                ("MATHPIX_APP_ID", settings.mathpix_app_id),
                ("MATHPIX_API_KEY", settings.mathpix_api_key),
            )
            if not value
        ]
        if missing:
            raise MissingCredentials(missing)
        return cls(settings.mathpix_app_id, settings.mathpix_api_key, **kwargs)

    def convert(self, pdf: Path, media_dir: Path) -> str:
        """Converts one PDF.

        Args:
            pdf: The PDF to send.
            media_dir: Where the images it refers to are written.

        Returns:
            The markdown, with every image reference rewritten to the bare
            basename of the file written into media_dir.

        Raises:
            MathpixError: On any refusal, timeout or conversion failure.
        """
        pdf_id = self._upload(pdf)
        self._wait(pdf_id)
        return self._localise_images(self._markdown(pdf_id), media_dir)

    def _request(
        self, method: str, url: str, step: str, *, auth: bool = True, **kwargs
    ) -> httpx.Response:
        """One request, with every failure named by the step it happened in."""
        try:
            response = self._client.request(
                method, url, headers=self._headers if auth else None, **kwargs
            )
        except httpx.TimeoutException as error:
            raise MathpixError(f"Mathpix timed out {step}: {error}") from error
        except httpx.HTTPError as error:
            raise MathpixError(f"Mathpix failed while {step}: {error}") from error

        if response.status_code == 401:
            raise MathpixError(
                f"Mathpix rejected the credentials while {step}: check "
                "MATHPIX_APP_ID and MATHPIX_API_KEY"
            )
        # Anything but 2xx, redirects included: httpx does not follow them, so a
        # redirected image would be written as an empty file and a redirected
        # upload or poll would raise decoding an empty body as JSON.
        if not response.is_success:
            detail = response.text.strip() or response.headers.get("location", "")
            raise MathpixError(
                f"Mathpix returned {response.status_code} while {step}"
                + (f": {detail}" if detail else "")
            )
        return response

    def _upload(self, pdf: Path) -> str:
        """Sends the PDF and returns the id Mathpix converts it under."""
        options = {"conversion_formats": {MARKDOWN_FORMAT: True}}
        with pdf.open("rb") as file:
            body = self._request(
                "POST",
                f"{self._base_url}/v3/pdf",
                f"uploading {pdf.name}",
                files={"file": (pdf.name, file, "application/pdf")},
                data={"options_json": json.dumps(options)},
            ).json()

        pdf_id = body.get("pdf_id")
        if not pdf_id:
            raise MathpixError(
                f"Mathpix returned no pdf_id for {pdf.name}: "
                f"{body.get('error') or body}"
            )
        return pdf_id

    def _wait(self, pdf_id: str) -> None:
        """Polls until the markdown is ready, or until the deadline passes."""
        give_up = self._clock() + self._deadline
        while True:
            body = self._request(
                "GET", f"{self._base_url}/v3/pdf/{pdf_id}", f"polling {pdf_id}"
            ).json()
            status = body.get("status")
            # conversion_status only appears once Mathpix starts the markdown,
            # so an absent one is another round of polling, not a failure.
            conversion = (body.get("conversion_status") or {}).get(
                MARKDOWN_FORMAT, {}
            )

            if status == "error":
                raise MathpixError(
                    f"Mathpix could not read {pdf_id}: "
                    f"{_reason(body) or 'no reason given'}"
                )
            if conversion.get("status") == "error":
                raise MathpixError(
                    f"Mathpix could not convert {pdf_id} to {MARKDOWN_FORMAT}: "
                    f"{_reason(conversion) or _reason(body) or 'no reason given'}"
                )
            if status == "completed" and conversion.get("status") == "completed":
                return
            if self._clock() >= give_up:
                raise MathpixError(
                    f"Mathpix did not finish {pdf_id} within {self._deadline:g}s "
                    f"(last status {status!r})"
                )
            self._sleep(self._poll_interval)

    def _markdown(self, pdf_id: str) -> str:
        """Fetches the converted markdown."""
        return self._request(
            "GET",
            f"{self._base_url}/v3/pdf/{pdf_id}.{MARKDOWN_FORMAT}",
            f"fetching markdown for {pdf_id}",
        ).text

    def _localise_images(self, markdown: str, media_dir: Path) -> str:
        """Downloads every image the markdown refers to, beside the markdown."""
        written: dict[str, str] = {}

        def replace(match: "re.Match[str]") -> str:
            url = match.group(2)
            if url not in written:
                written[url] = self._download(url, media_dir, set(written.values()))
            return f"![{match.group(1)}]({written[url]})"

        return IMAGE.sub(replace, markdown)

    def _download(self, url: str, media_dir: Path, taken: set) -> str:
        """Writes one image and returns the basename to refer to it by."""
        name = Path(urlsplit(url).path).name or "image"
        candidate, nth = name, 2
        # Two URLs can end in the same name; the later one takes a suffix.
        while candidate in taken:
            candidate = f"{Path(name).stem}-{nth}{Path(name).suffix}"
            nth += 1

        content = self._request(
            "GET", url, f"fetching image {name}", auth=False
        ).content
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / candidate).write_bytes(content)
        return candidate


def _reason(body: dict) -> str:
    """What Mathpix says went wrong, which it puts in error_info.message.

    The short `error` beside it is a code like `pdf_page_limit_exceeded`; older
    responses carry only that, so it is the fallback rather than the first look.
    """
    return (body.get("error_info") or {}).get("message") or body.get("error") or ""
