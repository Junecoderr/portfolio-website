-- Buffer settings for .tb files: tb fmt style (two spaces), '#' comments,
-- and tree-sitter highlighting and folds when the parser is installed.
vim.bo.commentstring = '# %s'
vim.bo.comments = ':##,:#'
vim.bo.expandtab = true
vim.bo.shiftwidth = 2
vim.bo.softtabstop = 2

if pcall(vim.treesitter.start) then
  vim.wo.foldexpr = 'v:lua.vim.treesitter.foldexpr()'
end

vim.b.undo_ftplugin = 'setl cms< com< et< sw< sts< fde<'
