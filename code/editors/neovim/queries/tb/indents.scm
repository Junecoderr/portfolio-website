; Indentation for TB (nvim-treesitter names). tb fmt uses two spaces.

[
  (block)
  (class_body)
  (match_expression)
  (enum_declaration)
  (map)
  (list)
  (arguments)
  (parameters)
] @indent.begin

[
  "}"
  "]"
  ")"
] @indent.branch @indent.end

(comment) @indent.auto
