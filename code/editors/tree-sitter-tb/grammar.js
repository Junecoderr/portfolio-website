/**
 * Tree-sitter grammar for TB 3.x.
 *
 * The reference is the real front end: code/tbcore/lexer.py and
 * code/tbcore/parser.py, summarised in code/spec/SPEC.md section 2.
 * The external scanner (src/scanner.c) reads text literals (TB's prefixes,
 * triple quotes and f-text holes) and the two line-break rules: a '(' or
 * '[' that starts a new line never calls or indexes the line before.
 */

const PREC = {
  lambda: -2,
  conditional: -1,
  or: 1,
  and: 2,
  not: 3,
  equality: 4,
  compare: 5,
  range: 6,
  bit_or: 7,
  bit_xor: 8,
  bit_and: 9,
  shift: 10,
  add: 11,
  mul: 12,
  unary: 13,
  power: 14,
  postfix: 15,
};

const ASSIGN_OPS = ['=', '+=', '-=', '*=', '/=', '**=', '//=', '%=', '&=', '|=', '^=', '<<=', '>>='];

const sep1 = (rule, separator) => seq(rule, repeat(seq(separator, rule)));
const commaSep1 = (rule) => sep1(rule, ',');
// A comma list that may end with a trailing comma (TB allows one almost everywhere).
const commaList = (rule) => optional(seq(commaSep1(rule), optional(',')));

module.exports = grammar({
  name: 'tb',

  word: ($) => $.identifier,

  extras: ($) => [/\s/, $.comment],

  externals: ($) => [
    $.string,
    $.fstring_start,
    $.string_content,
    $.fstring_end,
    $.format_spec,
    $._call_lparen,
    $._index_lbracket,
    // Inside a match, '-' or 'is' at the start of a line ends the arm before
    // it ('-1 => ...' and 'is text => ...' are the next arm's patterns).
    $._arm_end,
    $._error_sentinel,
  ],

  supertypes: ($) => [$._statement, $._expression, $._match_pattern],

  conflicts: ($) => [
    // '(a, b) => ...' (a lambda) and '(a)' (brackets) start the same way.
    [$._expression, $._binding],
    [$.list, $.list_pattern],
    [$.rest_pattern, $._expression],
    [$.map, $.map_pattern],
    [$.map, $.block],
    [$._expression, $.map_pattern_entry],
  ],

  rules: {
    source_file: ($) => repeat(choice($._statement, ';')),

    // ---------------------------------------------------------------- statements

    _statement: ($) =>
      choice(
        $.let_declaration,
        $.function_declaration,
        $.class_declaration,
        $.enum_declaration,
        $.export_statement,
        $.import_statement,
        $.from_import,
        $.return_statement,
        $.if_statement,
        $.while_statement,
        $.for_statement,
        $.try_statement,
        $.throw_statement,
        $.defer_statement,
        $.assert_statement,
        $.break_statement,
        $.continue_statement,
        $.assignment,
        $.multi_assignment,
        $.expression_statement,
      ),

    block: ($) => seq('{', repeat(choice($._statement, ';')), '}'),

    let_declaration: ($) =>
      seq(
        choice('let', 'const'),
        choice(
          seq(field('name', $.identifier), optional(seq(':', field('type', $._type)))),
          field('pattern', choice($.list_pattern, $.map_pattern, $.name_list)),
        ),
        '=',
        field('value', $._expression),
      ),

    // let a, b = pair
    name_list: ($) => seq($.identifier, repeat1(seq(',', $._binding))),

    _binding: ($) => choice($.identifier, $.list_pattern, $.map_pattern),

    list_pattern: ($) => seq('[', commaList(choice($._binding, $.rest_pattern)), ']'),

    rest_pattern: ($) => seq('...', optional($.identifier)),

    map_pattern: ($) => seq('{', commaList($.map_pattern_entry), '}'),

    map_pattern_entry: ($) =>
      seq(
        field('key', choice($.identifier, $.string)),
        optional(seq(':', field('value', $._binding))),
        optional(seq('=', field('default', $._expression))),
      ),

    function_declaration: ($) =>
      seq(
        optional('async'),
        'fn',
        field('name', $.identifier),
        field('parameters', $.parameters),
        optional(seq('->', field('return_type', $._type))),
        field('body', $.block),
      ),

    parameters: ($) => seq('(', commaList(choice($.parameter, $.rest_parameter, $.kwrest_parameter)), ')'),

    parameter: ($) =>
      seq(
        field('name', $._binding),
        optional(seq(':', field('type', $._type))),
        optional(seq('=', field('default', $._expression))),
      ),

    rest_parameter: ($) => seq('...', field('name', $.identifier)),

    kwrest_parameter: ($) => seq('**', field('name', $.identifier)),

    class_declaration: ($) =>
      seq(
        'class',
        field('name', $.identifier),
        optional(seq('extends', field('superclass', $.type_name))),
        field('body', $.class_body),
      ),

    class_body: ($) => seq('{', repeat(choice($.field_declaration, $.method_declaration, ';')), '}'),

    field_declaration: ($) =>
      seq(
        optional('static'),
        choice('let', 'const'),
        field('name', $.identifier),
        optional(seq(':', field('type', $._type))),
        optional(seq('=', field('value', $._expression))),
      ),

    method_declaration: ($) =>
      seq(
        optional('static'),
        optional('async'),
        'fn',
        field('name', $.identifier),
        field('parameters', $.parameters),
        optional(seq('->', field('return_type', $._type))),
        field('body', $.block),
      ),

    enum_declaration: ($) =>
      seq('enum', field('name', $.identifier), '{', commaList($.enum_member), '}'),

    enum_member: ($) => $.identifier,

    export_statement: ($) =>
      seq('export', choice($.let_declaration, $.function_declaration, $.class_declaration, $.enum_declaration)),

    import_statement: ($) =>
      seq(
        'import',
        field('module', choice($.identifier, $.string)),
        optional(seq('as', field('alias', $.identifier))),
      ),

    from_import: ($) =>
      seq('from', field('module', choice($.identifier, $.string)), 'import', commaSep1($.import_name)),

    import_name: ($) => seq(field('name', $.identifier), optional(seq('as', field('alias', $.identifier)))),

    return_statement: ($) => prec.right(seq('return', optional(commaSep1($._expression)))),

    if_statement: ($) =>
      prec.right(
        seq(
          'if',
          field('condition', $._expression),
          field('consequence', $.block),
          optional(field('alternative', $.else_clause)),
        ),
      ),

    else_clause: ($) => seq('else', choice($.if_statement, $.block)),

    while_statement: ($) => seq('while', field('condition', $._expression), field('body', $.block)),

    for_statement: ($) =>
      seq(
        'for',
        field('left', commaSep1($._binding)),
        'in',
        field('right', $._expression),
        field('body', $.block),
      ),

    try_statement: ($) =>
      prec.right(seq('try', field('body', $.block), repeat($.catch_clause), optional($.finally_clause))),

    catch_clause: ($) =>
      seq(
        'catch',
        optional(seq(field('name', $.identifier), optional(seq(':', sep1(field('type', $.type_name), 'or'))))),
        field('body', $.block),
      ),

    finally_clause: ($) => seq('finally', field('body', $.block)),

    throw_statement: ($) => prec.right(seq('throw', $._expression)),

    defer_statement: ($) => seq('defer', field('body', $.block)),

    assert_statement: ($) =>
      prec.right(seq('assert', field('condition', $._expression), optional(seq(',', field('message', $._expression))))),

    break_statement: (_) => 'break',

    continue_statement: (_) => 'continue',

    assignment: ($) =>
      seq(field('left', $._expression), field('operator', choice(...ASSIGN_OPS)), field('right', $._expression)),

    // a, b = b, a
    multi_assignment: ($) =>
      seq(
        field('left', seq($._expression, repeat1(seq(',', $._expression)))),
        '=',
        field('right', commaSep1($._expression)),
      ),

    expression_statement: ($) => $._expression,

    // --------------------------------------------------------------------- types

    _type: ($) => choice($.union_type, $._single_type),

    union_type: ($) => prec.left(seq($._type, '|', $._type)),

    _single_type: ($) => choice($.type_name, $.generic_type, $.function_type, $.parenthesized_type),

    type_name: ($) => prec.right(choice('nil', 'class', sep1($.identifier, '.'))),

    generic_type: ($) => seq($.type_name, '<', commaSep1($._type), '>'),

    function_type: ($) =>
      prec.right(seq('fn', '(', optional(commaSep1($._type)), ')', optional(seq('->', $._type)))),

    parenthesized_type: ($) => seq('(', $._type, ')'),

    // --------------------------------------------------------------- expressions

    _expression: ($) =>
      choice(
        $.conditional_expression,
        $.yield_expression,
        $.lambda,
        $.not_expression,
        $.binary_expression,
        $.is_expression,
        $.unary_expression,
        $.await_expression,
        $.call,
        $.index,
        $.slice,
        $.member,
        $.super,
        $.self,
        $.identifier,
        $.number,
        $.string,
        $.fstring,
        $.true,
        $.false,
        $.nil,
        $.list,
        $.map,
        $.parenthesized_expression,
        $.function_expression,
        $.match_expression,
      ),

    conditional_expression: ($) =>
      prec.right(
        PREC.conditional,
        seq(
          'if',
          field('condition', $._expression),
          'then',
          field('consequence', $._expression),
          'else',
          field('alternative', $._expression),
        ),
      ),

    yield_expression: ($) => prec.right(PREC.conditional, seq('yield', optional($._expression))),

    lambda: ($) =>
      prec.right(
        PREC.lambda,
        seq(
          field('parameters', choice($.identifier, $.parameters)),
          '=>',
          field('body', choice($.block, $._expression)),
        ),
      ),

    not_expression: ($) => prec(PREC.not, seq('not', field('argument', $._expression))),

    binary_expression: ($) => {
      const table = [
        [PREC.or, 'or'],
        [PREC.and, 'and'],
        [PREC.equality, choice('==', '!=')],
        [PREC.compare, choice('<', '<=', '>', '>=', 'in', seq('not', 'in'))],
        [PREC.range, choice('..', '..=')],
        [PREC.bit_or, '|'],
        [PREC.bit_xor, '^'],
        [PREC.bit_and, '&'],
        [PREC.shift, choice('<<', '>>')],
        [PREC.add, choice('+', '-')],
        [PREC.mul, choice('*', '/', '//', '%')],
      ];
      return choice(
        ...table.map(([p, op]) =>
          prec.left(p, seq(field('left', $._expression), field('operator', op), field('right', $._expression))),
        ),
        prec.right(
          PREC.power,
          seq(field('left', $._expression), field('operator', '**'), field('right', $._expression)),
        ),
      );
    },

    is_expression: ($) =>
      prec.left(
        PREC.compare,
        seq(field('left', $._expression), 'is', optional('not'), field('type', $.type_name)),
      ),

    unary_expression: ($) =>
      prec(PREC.unary, seq(field('operator', choice('-', '!', '~')), field('argument', $._expression))),

    await_expression: ($) => prec(PREC.unary, seq('await', $._expression)),

    call: ($) =>
      prec(PREC.postfix, seq(field('function', $._expression), field('arguments', $.arguments))),

    arguments: ($) =>
      seq(
        alias($._call_lparen, '('),
        commaList(choice($._expression, $.spread, $.keyword_argument)),
        ')',
      ),

    keyword_argument: ($) => seq(field('name', $.identifier), '=', field('value', $._expression)),

    spread: ($) => seq('...', $._expression),

    index: ($) =>
      prec(
        PREC.postfix,
        seq(field('object', $._expression), alias($._index_lbracket, '['), field('index', $._expression), ']'),
      ),

    slice: ($) =>
      prec(
        PREC.postfix,
        seq(
          field('object', $._expression),
          alias($._index_lbracket, '['),
          optional(field('start', $._expression)),
          ':',
          optional(field('stop', $._expression)),
          optional(seq(':', optional(field('step', $._expression)))),
          ']',
        ),
      ),

    member: ($) =>
      prec(PREC.postfix, seq(field('object', $._expression), '.', field('property', $.identifier))),

    super: ($) => seq('super', '.', field('property', $.identifier)),

    self: (_) => 'self',

    parenthesized_expression: ($) => seq('(', $._expression, ')'),

    list: ($) => seq('[', commaList(choice($._expression, $.spread)), ']'),

    map: ($) => seq('{', commaList(choice($.pair, $.spread)), '}'),

    pair: ($) => seq(field('key', $._expression), ':', field('value', $._expression)),

    function_expression: ($) =>
      seq(
        optional('async'),
        'fn',
        field('parameters', $.parameters),
        optional(seq('->', field('return_type', $._type))),
        field('body', $.block),
      ),

    // --------------------------------------------------------------------- match

    match_expression: ($) =>
      seq('match', field('subject', $._expression), '{', repeat(choice($.match_arm, ',')), '}'),

    match_arm: ($) =>
      seq(
        field('pattern', $._match_pattern),
        optional(seq('if', field('guard', $._expression))),
        '=>',
        choice(field('body', $.block), seq(field('body', $._expression), optional($._arm_end))),
      ),

    _match_pattern: ($) =>
      choice(
        $.or_pattern,
        $.literal_pattern,
        $.range_pattern,
        $.list_match_pattern,
        $.map_match_pattern,
        $.class_pattern,
        $.is_pattern,
        $.value_pattern,
        $.parenthesized_pattern,
        $.identifier,
      ),

    or_pattern: ($) => prec.left(seq($._match_pattern, 'or', $._match_pattern)),

    literal_pattern: ($) =>
      choice(seq(optional('-'), $.number), $.string, $.true, $.false, $.nil),

    range_pattern: ($) =>
      seq(
        field('start', seq(optional('-'), $.number)),
        choice('..', '..='),
        field('end', seq(optional('-'), $.number)),
      ),

    list_match_pattern: ($) => seq('[', commaList(choice($._match_pattern, $.rest_pattern)), ']'),

    map_match_pattern: ($) => seq('{', commaList($.map_match_entry), '}'),

    map_match_entry: ($) =>
      seq(field('key', choice($.identifier, $.string, $.number)), optional(seq(':', field('value', $._match_pattern)))),

    class_pattern: ($) =>
      seq(
        field('class', choice($.identifier, $.dotted_name)),
        choice(
          seq('(', optional($._match_pattern), ')'),
          seq('{', commaList($.field_pattern), '}'),
        ),
      ),

    field_pattern: ($) =>
      seq(field('name', $.identifier), optional(seq(':', field('value', $._match_pattern)))),

    is_pattern: ($) => seq('is', $.type_name),

    value_pattern: ($) => $.dotted_name,

    dotted_name: ($) => seq($.identifier, repeat1(seq('.', $.identifier))),

    parenthesized_pattern: ($) => seq('(', $._match_pattern, ')'),

    // ------------------------------------------------------------------ literals

    fstring: ($) =>
      seq($.fstring_start, repeat(choice($.string_content, $.interpolation)), $.fstring_end),

    interpolation: ($) =>
      seq(
        '{',
        field('expression', $._expression),
        optional(field('conversion', '!r')),
        optional(seq(':', optional($.format_spec))),
        '}',
      ),

    number: (_) =>
      token(
        choice(
          /0[xX][0-9a-fA-F_]+/,
          /0[bB][01_]+/,
          /0[oO][0-7_]+/,
          /[0-9][0-9_]*(\.[0-9][0-9_]*)?([eE][+-]?[0-9]+)?/,
        ),
      ),

    true: (_) => 'true',
    false: (_) => 'false',
    nil: (_) => 'nil',

    identifier: (_) => /[\p{L}_][\p{L}\p{N}_]*/,

    comment: (_) => token(seq('#', /.*/)),
  },
});
