; Highlights for TB, with nvim-treesitter capture names.
; General patterns first, specific ones later: when two patterns capture the
; same node, the later one wins (Neovim and the tree-sitter CLI agree). The
; most specific ones also carry priority 101 for Neovim.

; Definitions ----------------------------------------------------------------

(function_declaration
  name: (identifier) @function)

(method_declaration
  name: (identifier) @function.method)

(class_declaration
  name: (identifier) @type)

(enum_declaration
  name: (identifier) @type)

(enum_member
  (identifier) @constant)

(field_declaration
  name: (identifier) @variable.member)

(parameter
  name: (identifier) @variable.parameter)

(rest_parameter
  name: (identifier) @variable.parameter)

(kwrest_parameter
  name: (identifier) @variable.parameter)

(lambda
  parameters: (identifier) @variable.parameter)

(keyword_argument
  name: (identifier) @variable.parameter)

; Uses -----------------------------------------------------------------------

(member
  property: (identifier) @variable.member)

(call
  function: (identifier) @function.call)

(super
  property: (identifier) @function.method.call)

(type_name
  (identifier) @type)

(type_name
  [
    "nil"
    "class"
  ] @type.builtin)

(class_pattern
  class: (identifier) @type)

(field_pattern
  name: (identifier) @variable.member)

(import_statement
  module: (identifier) @module)

(import_statement
  alias: (identifier) @module)

(from_import
  module: (identifier) @module)

; Literals -------------------------------------------------------------------

(comment) @comment

[
  (string)
  (string_content)
  (fstring_start)
  (fstring_end)
] @string

(format_spec) @string.special

(number) @number

[
  (true)
  (false)
] @boolean

(nil) @constant.builtin

(self) @variable.builtin

(super
  "super" @variable.builtin)

; Keywords -------------------------------------------------------------------

[
  "let"
  "const"
  "class"
  "enum"
  "extends"
  "static"
  "defer"
  "assert"
] @keyword

"fn" @keyword.function

[
  "return"
  "yield"
] @keyword.return

[
  "if"
  "else"
  "then"
  "match"
] @keyword.conditional

[
  "while"
  "for"
  (break_statement)
  (continue_statement)
] @keyword.repeat

[
  "import"
  "from"
  "export"
  "as"
] @keyword.import

[
  "try"
  "catch"
  "finally"
  "throw"
] @keyword.exception

[
  "async"
  "await"
] @keyword.coroutine

[
  "and"
  "or"
  "not"
  "in"
  "is"
] @keyword.operator

; Operators and punctuation --------------------------------------------------

[
  "+" "-" "*" "/" "//" "%" "**" "&" "|" "^" "~" "<<" ">>"
  "==" "!=" "<" "<=" ">" ">=" "!"
  "=" "+=" "-=" "*=" "/=" "//=" "%=" "**=" "&=" "|=" "^=" "<<=" ">>="
  ".." "..=" "..." "=>" "->"
] @operator

[
  "("
  ")"
  "["
  "]"
  "{"
  "}"
] @punctuation.bracket

[
  ","
  "."
  ":"
  ";"
] @punctuation.delimiter

(interpolation
  [
    "{"
    "}"
  ] @punctuation.special)

(interpolation
  "!r" @punctuation.special)

; Specific identifiers (last, so they win) -----------------------------------

((call
  function: (member
    property: (identifier) @function.method.call))
  (#set! priority 101))

((call
  function: (identifier) @constructor)
  (#match? @constructor "^[A-Z]")
  (#set! priority 101))

((call
  function: (identifier) @function.builtin)
  (#any-of? @function.builtin
    "say" "ask" "len" "str" "num" "int" "float" "range" "push" "care" "type"
    "keys" "round" "min" "max" "sum" "sorted" "exit" "format")
  (#set! priority 101))

((identifier) @constant
  (#match? @constant "^[A-Z][A-Z0-9_]+$")
  (#set! priority 101))

((identifier) @type.builtin
  (#any-of? @type.builtin
    "Error" "TypeError" "ValueError" "FileError" "ParseError" "NetworkError"
    "PermissionError" "NotFoundError" "TimeoutError" "StopIteration" "AssertionError")
  (#set! priority 101))

((match_arm
  pattern: (identifier) @character.special)
  (#eq? @character.special "_")
  (#set! priority 101))

((comment) @comment.documentation
  (#match? @comment.documentation "^##")
  (#set! priority 101))
