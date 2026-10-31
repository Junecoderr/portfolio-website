; Highlights for TB in Helix (25.07+: later patterns win, like Neovim).
; Made from code/editors/tree-sitter-tb/queries/highlights.scm with Helix's
; scope names; keep the two in step.

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
  name: (identifier) @variable.other.member)

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
  property: (identifier) @variable.other.member)

(call
  function: (identifier) @function)

(super
  property: (identifier) @function.method)

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
  name: (identifier) @variable.other.member)

(import_statement
  module: (identifier) @namespace)

(import_statement
  alias: (identifier) @namespace)

(from_import
  module: (identifier) @namespace)

; Literals -------------------------------------------------------------------

(comment) @comment

[
  (string)
  (string_content)
  (fstring_start)
  (fstring_end)
] @string

(format_spec) @string.special

(number) @constant.numeric

[
  (true)
  (false)
] @constant.builtin.boolean

(nil) @constant.builtin

(self) @variable.builtin

(super
  "super" @variable.builtin)

; Keywords -------------------------------------------------------------------

[
  "let"
  "class"
  "enum"
] @keyword.storage.type

[
  "const"
  "static"
] @keyword.storage.modifier

[
  "extends"
  "defer"
  "assert"
] @keyword

"fn" @keyword.function

[
  "return"
  "yield"
] @keyword.control.return

[
  "if"
  "else"
  "then"
  "match"
] @keyword.control.conditional

[
  "while"
  "for"
  (break_statement)
  (continue_statement)
] @keyword.control.repeat

[
  "import"
  "from"
  "export"
  "as"
] @keyword.control.import

[
  "try"
  "catch"
  "finally"
  "throw"
] @keyword.control.exception

[
  "async"
  "await"
] @keyword.control

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
    property: (identifier) @function.method)))

((call
  function: (identifier) @constructor)
  (#match? @constructor "^[A-Z]"))

((call
  function: (identifier) @function.builtin)
  (#any-of? @function.builtin
    "say" "ask" "len" "str" "num" "int" "float" "range" "push" "care" "type"
    "keys" "round" "min" "max" "sum" "sorted" "exit" "format"))

((identifier) @constant
  (#match? @constant "^[A-Z][A-Z0-9_]+$"))

((identifier) @type.builtin
  (#any-of? @type.builtin
    "Error" "TypeError" "ValueError" "FileError" "ParseError" "NetworkError"
    "PermissionError" "NotFoundError" "TimeoutError" "StopIteration" "AssertionError"))

((match_arm
  pattern: (identifier) @special)
  (#eq? @special "_"))

((comment) @comment.line.documentation
  (#match? @comment.line.documentation "^##"))
