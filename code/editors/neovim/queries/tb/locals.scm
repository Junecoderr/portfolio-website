; Scopes, definitions and references for TB (nvim-treesitter names).

[
  (source_file)
  (block)
  (function_declaration)
  (method_declaration)
  (function_expression)
  (lambda)
  (class_body)
  (match_arm)
  (for_statement)
  (catch_clause)
] @local.scope

(let_declaration
  name: (identifier) @local.definition.var)

(name_list
  (identifier) @local.definition.var)

(list_pattern
  (identifier) @local.definition.var)

(rest_pattern
  (identifier) @local.definition.var)

(map_pattern_entry
  key: (identifier) @local.definition.var
  !value)

(for_statement
  left: (identifier) @local.definition.var)

(catch_clause
  name: (identifier) @local.definition.var)

(function_declaration
  name: (identifier) @local.definition.function)

(method_declaration
  name: (identifier) @local.definition.method)

(class_declaration
  name: (identifier) @local.definition.type)

(enum_declaration
  name: (identifier) @local.definition.type)

(parameter
  name: (identifier) @local.definition.parameter)

(rest_parameter
  name: (identifier) @local.definition.parameter)

(kwrest_parameter
  name: (identifier) @local.definition.parameter)

(lambda
  parameters: (identifier) @local.definition.parameter)

(import_statement
  module: (identifier) @local.definition.import
  !alias)

(import_statement
  alias: (identifier) @local.definition.import)

(import_name
  name: (identifier) @local.definition.import
  !alias)

(import_name
  alias: (identifier) @local.definition.import)

(match_arm
  pattern: (identifier) @local.definition.var)

(identifier) @local.reference
