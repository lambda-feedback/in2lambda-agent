-- A filter of the shape the model writes for a folder: it reads the sheets by
-- their Question headings, and the solutions document, under the role pandoc
-- passes it, by the question number each answer paragraph opens with.
local function esc(s)
  return (s:gsub('[%z\1-\31"\\]', function(c)
    if c == '"' then return '\\"'
    elseif c == '\\' then return '\\\\'
    elseif c == '\n' then return '\\n'
    else return string.format('\\u%04x', string.byte(c))
    end
  end))
end

local function render(blocks)
  local md = pandoc.write(pandoc.Pandoc(blocks), 'commonmark_x', { wrap_text = 'wrap-none' })
  return (md:gsub('^%s+', ''):gsub('%s+$', ''))
end

local function questions(doc)
  local out, current = {}, nil
  for _, b in ipairs(doc.blocks) do
    if b.t == 'Header' then
      current = nil
      if pandoc.utils.stringify(b.content):match('^Question') then
        current = {}
        out[#out + 1] = current
      end
    elseif current then
      current[#current + 1] = b
    end
  end
  local pieces = {}
  for _, blocks in ipairs(out) do
    pieces[#pieces + 1] = '{"title": "", "main_text": "' .. esc(render(blocks)) .. '", "parts": []}'
  end
  return pieces
end

local function solutions(doc)
  local answers, inside = {}, false
  for _, b in ipairs(doc.blocks) do
    if b.t == 'Header' then
      inside = pandoc.utils.stringify(b.content):match('^Solutions') ~= nil
    elseif inside and (b.t == 'Para' or b.t == 'Plain') then
      local text = render({ b })
      local n = tonumber(text:match('^(%d+)'))
      if n then
        answers[n] = answers[n] or {}
        table.insert(answers[n], text)
      end
    end
  end
  local pieces = {}
  for i = 1, #answers do
    pieces[#pieces + 1] = '{"title": "", "main_text": "", "parts": [{"content": "", "options": [], "answer": "", "worked_solution": "'
      .. esc(table.concat(answers[i], '\n\n')) .. '"}]}'
  end
  return pieces
end

function Pandoc(doc)
  local role = pandoc.utils.stringify(doc.meta.in2lambda_role or 'questions')
  local pieces = role == 'solutions' and solutions(doc) or questions(doc)
  return pandoc.Pandoc({ pandoc.CodeBlock('[' .. table.concat(pieces, ',\n') .. ']') })
end
