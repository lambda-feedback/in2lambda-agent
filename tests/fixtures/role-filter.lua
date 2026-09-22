-- Echoes back the role pandoc was given, as a question title, which is how the
-- test tells the run over the questions from the run over the solutions.
function Pandoc(doc)
  local role = pandoc.utils.stringify(doc.meta.in2lambda_role or '')
  local json = '[{"title": "' .. role .. '", "main_text": "", "parts": []}]'
  return pandoc.Pandoc({ pandoc.CodeBlock(json) })
end
