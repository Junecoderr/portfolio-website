-- `tb lsp` for Neovim 0.11+ (picked up by vim.lsp.enable('tb')).
-- Set vim.g.tb_lsp_cmd (a list) before it starts to run another `tb`.
return {
  cmd = vim.g.tb_lsp_cmd or { 'tb', 'lsp' },
  filetypes = { 'tb' },
  root_markers = { 'tb.toml', '.git' },
}
