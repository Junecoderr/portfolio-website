-- TB for Neovim 0.11+: .tb files, the tree-sitter parser (parser/tb.so on
-- the runtimepath) and the language server `tb lsp`.
if vim.g.loaded_tb then
  return
end
vim.g.loaded_tb = true

vim.filetype.add({ extension = { tb = 'tb' }, filename = { ['tb.lock'] = 'toml' } })

if vim.g.tb_lsp ~= false then
  vim.lsp.enable('tb')
end
