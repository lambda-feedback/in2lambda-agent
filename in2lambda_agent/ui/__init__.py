"""The local web page for trying the agent by hand.

`in2lambda-agent ui` serves one page on 127.0.0.1: a source picker, the run's
options, the stage lines as they arrive, the questions of a waiting review, and
links to what the run wrote. It is a development harness, not a product
feature: there is no authentication, and one run at a time.

The page needs Starlette and uvicorn, which are the `ui` extra:
`poetry install --extras ui`. Nothing outside this package imports either, so
every other command runs without them.
"""
