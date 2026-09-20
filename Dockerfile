FROM python:3.12-slim

# pandoc reads the source document; poppler-utils gives pdftoppm, which renders
# a PDF's pages for `compare`; Node runs the KaTeX check the design spec
# lists; xelatex plus these TeX packages are every package the PDF generator's
# src/template.latex loads — braket and cancel from texlive-science, xeCJK and
# ctex from texlive-lang-chinese, ulem from texlive-plain-generic, biblatex from
# texlive-bibtex-extra, bidi from texlive-lang-arabic, lmodern from its own
# package, and the rest from the recommended and extra sets. Noto Sans is what
# the template asks for when a document names no font.
RUN apt-get update && apt-get install --no-install-recommends -y \
    pandoc \
    poppler-utils \
    nodejs \
    texlive-xetex \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-science \
    texlive-lang-chinese \
    texlive-lang-arabic \
    texlive-bibtex-extra \
    texlive-plain-generic \
    texlive-fonts-recommended \
    lmodern \
    fonts-noto-core \
    fonts-noto-cjk \
    git \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

WORKDIR /app
COPY pyproject.toml README.md ./
COPY in2lambda_agent ./in2lambda_agent
RUN poetry config virtualenvs.create false && poetry install --without dev

ENTRYPOINT ["in2lambda-agent"]
