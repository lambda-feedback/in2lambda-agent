-- Keep the words of an underlined run and drop the underline, which Lambda Feedback
-- renders in no form pandoc can write. See markdown_of in routes.py.
function Underline(el)
  return el.content
end
