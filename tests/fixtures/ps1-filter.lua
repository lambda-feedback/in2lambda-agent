local function esc(s)
  s = s:gsub('[%z\1-\31"\\]', function(c)
    if c == '"' then return '\\"'
    elseif c == '\\' then return '\\\\'
    elseif c == '\n' then return '\\n'
    elseif c == '\r' then return '\\r'
    elseif c == '\t' then return '\\t'
    elseif c == '\b' then return '\\b'
    elseif c == '\f' then return '\\f'
    else return string.format('\\u%04x', string.byte(c))
    end
  end)
  return s
end

local function is_blank(b)
  if b.t == 'Para' or b.t == 'Plain' then
    return #pandoc.utils.stringify(b):gsub('%s', '') == 0
      and #b.content:filter(function(i)
            return i.t == 'Image' or i.t == 'Math' or i.t == 'RawInline'
          end) == 0
  end
  return false
end

local function render(blocks)
  local keep = {}
  for _, b in ipairs(blocks) do
    if not is_blank(b) then keep[#keep + 1] = b end
  end
  if #keep == 0 then return '' end
  local md = pandoc.write(pandoc.Pandoc(keep), 'commonmark_x')
  md = md:gsub('^%s+', ''):gsub('%s+$', '')
  return md
end

local function split_item(item)
  local own, nested = {}, nil
  for _, b in ipairs(item) do
    if b.t == 'OrderedList' and nested == nil then
      nested = b.content
    elseif b.t == 'BulletList' and nested == nil then
      nested = b.content
    else
      own[#own + 1] = b
    end
  end
  return own, nested
end

local function find_questions(blocks)
  for _, b in ipairs(blocks) do
    if b.t == 'OrderedList' then return b.content end
  end
  return nil
end

function Pandoc(doc)
  local questions = find_questions(doc.blocks) or {}
  local out = {}
  for _, item in ipairs(questions) do
    local own, nested = split_item(item)
    local piece = '{"title": "", "main_text": "' .. esc(render(own)) .. '"'
    if nested and #nested > 0 then
      local parts = {}
      for _, sub in ipairs(nested) do
        local sown = split_item(sub)
        parts[#parts + 1] = '{"content": "' .. esc(render(sown)) .. '"}'
      end
      piece = piece .. ', "parts": [' .. table.concat(parts, ', ') .. ']'
    else
      piece = piece .. ', "parts": []'
    end
    out[#out + 1] = piece .. '}'
  end
  local json = '[' .. table.concat(out, ',\n') .. ']'
  return pandoc.Pandoc({ pandoc.CodeBlock(json) })
end