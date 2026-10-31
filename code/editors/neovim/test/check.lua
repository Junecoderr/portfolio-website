-- Headless check of the TB Neovim setup: opens test/sample.tb, then checks
-- that tree-sitter highlighting is on (with the expected captures) and that
-- `tb lsp` attaches and answers hover, definition and diagnostics.
--
--   nvim --headless --clean --cmd "set rtp^=code/editors/neovim,<dir with parser/tb.so>" \
--     -c "luafile code/editors/neovim/test/check.lua"
--
-- Prints one JSON line of evidence; exits 1 if anything is missing.

local here = debug.getinfo(1, 'S').source:sub(2):match('(.*[/\\])') or './'
local result = { ok = true, failures = {} }

local function fail(what)
  result.ok = false
  table.insert(result.failures, what)
end

local function finish()
  io.stdout:write(vim.json.encode(result) .. '\n')
  vim.cmd(result.ok and 'qa!' or 'cquit 1')
end

local ok, err = pcall(function()
  vim.cmd('edit ' .. vim.fn.fnameescape(here .. 'sample.tb'))
  local buf = vim.api.nvim_get_current_buf()
  result.filetype = vim.bo[buf].filetype
  if result.filetype ~= 'tb' then fail('filetype is ' .. result.filetype) end

  -- tree-sitter
  result.highlighter = vim.treesitter.highlighter.active[buf] ~= nil
  if not result.highlighter then fail('tree-sitter highlighting is not active') end
  local tree = vim.treesitter.get_parser(buf, 'tb'):parse()[1]
  result.parse_error = tree:root():has_error()
  if result.parse_error then fail('the sample has ERROR nodes') end
  local want = {
    { 0, 0, 'comment.documentation' },  -- ## Adds two numbers.
    { 1, 0, 'keyword.function' },       -- fn
    { 1, 3, 'function' },               -- add
    { 1, 7, 'variable.parameter' },     -- a
    { 2, 2, 'keyword.return' },         -- return
    { 5, 12, 'function.call' },         -- add(
    { 5, 16, 'number' },                -- 1
    { 6, 0, 'function.builtin' },       -- say
  }
  result.captures = {}
  for _, w in ipairs(want) do
    local names = {}
    for _, c in ipairs(vim.treesitter.get_captures_at_pos(buf, w[1], w[2])) do
      table.insert(names, c.capture)
    end
    result.captures[string.format('%d:%d', w[1], w[2])] = names
    if not vim.tbl_contains(names, w[3]) then
      fail(string.format('no @%s at %d:%d (got %s)', w[3], w[1], w[2], table.concat(names, ',')))
    end
  end

  -- language server
  local attached = vim.wait(15000, function()
    local clients = vim.lsp.get_clients({ bufnr = buf, name = 'tb' })
    return #clients > 0 and clients[1].initialized
  end, 50)
  result.lsp_attached = attached
  if not attached then
    fail('tb lsp did not attach')
    return
  end
  local client = vim.lsp.get_clients({ bufnr = buf, name = 'tb' })[1]
  result.server = client.server_info
  result.cmd = client.config.cmd

  local function request(method, line, col)
    local params = {
      textDocument = { uri = vim.uri_from_bufnr(buf) },
      position = { line = line, character = col },
    }
    local replies = vim.lsp.buf_request_sync(buf, method, params, 10000) or {}
    for _, reply in pairs(replies) do
      if reply.result then return reply.result end
    end
  end

  local hover = request('textDocument/hover', 5, 13)  -- on add(1, 2)
  local text = hover and hover.contents and (hover.contents.value or hover.contents) or ''
  if type(text) ~= 'string' then text = vim.inspect(text) end
  result.hover = text
  if not text:find('add') then fail('hover did not describe add') end

  local def = request('textDocument/definition', 5, 13)
  if def and def.uri == nil and def[1] then def = def[1] end
  result.definition_line = def and def.range and def.range.start.line
  if result.definition_line ~= 1 then fail('definition of add is not line 2') end

  vim.wait(10000, function() return #vim.diagnostic.get(buf) > 0 end, 50)
  result.diagnostics = {}
  for _, d in ipairs(vim.diagnostic.get(buf)) do
    table.insert(result.diagnostics, string.format('%d: %s', d.lnum + 1, d.message))
  end
  if #result.diagnostics == 0 then fail('no diagnostics for the misspelled name') end
end)

if not ok then fail('error: ' .. tostring(err)) end
finish()
