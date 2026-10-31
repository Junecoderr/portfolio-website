// External scanner for tree-sitter-tb.
//
// It reads what a regular expression can't: TB text literals (prefixes f/r/b,
// triple quotes, f-text holes, nested f-texts) and the line-break rule that a
// '(' or '[' starting a new line never calls or indexes the line before.

#include "tree_sitter/parser.h"

#include <stdlib.h>
#include <string.h>
#include <wctype.h>

enum TokenType {
  STRING,
  FSTRING_START,
  STRING_CONTENT,
  FSTRING_END,
  FORMAT_SPEC,
  CALL_LPAREN,
  INDEX_LBRACKET,
  ARM_END,
  ERROR_SENTINEL,
};

// One byte per open f-text: the quote character ('"' or '\'') plus flags.
#define TRIPLE 0x40
#define RAW 0x80
#define QUOTE_MASK 0x3f
#define MAX_DEPTH 64

typedef struct {
  uint8_t depth;
  uint8_t stack[MAX_DEPTH];
} Scanner;

static inline void advance(TSLexer *lexer) { lexer->advance(lexer, false); }
static inline void skip(TSLexer *lexer) { lexer->advance(lexer, true); }

void *tree_sitter_tb_external_scanner_create(void) { return calloc(1, sizeof(Scanner)); }

void tree_sitter_tb_external_scanner_destroy(void *payload) { free(payload); }

unsigned tree_sitter_tb_external_scanner_serialize(void *payload, char *buffer) {
  Scanner *s = (Scanner *)payload;
  buffer[0] = (char)s->depth;
  memcpy(buffer + 1, s->stack, s->depth);
  return 1 + s->depth;
}

void tree_sitter_tb_external_scanner_deserialize(void *payload, const char *buffer, unsigned length) {
  Scanner *s = (Scanner *)payload;
  s->depth = 0;
  if (length == 0) return;
  uint8_t depth = (uint8_t)buffer[0];
  if (depth > MAX_DEPTH || depth + 1u > length) depth = 0;
  s->depth = depth;
  memcpy(s->stack, buffer + 1, depth);
}

// The text between the quotes of an f-text, up to a hole, the closing
// quote(s) or (for a one-line f-text) the end of the line.
static bool scan_fstring_body(Scanner *s, TSLexer *lexer, const bool *valid) {
  uint8_t top = s->stack[s->depth - 1];
  int32_t quote = top & QUOTE_MASK;
  bool triple = top & TRIPLE, raw = top & RAW;
  bool any = false;
  lexer->result_symbol = STRING_CONTENT;
  for (;;) {
    if (lexer->eof(lexer)) return any;
    int32_t c = lexer->lookahead;
    if (c == quote) {
      if (!triple) {
        if (any) return true;
        if (!valid[FSTRING_END]) return false;
        advance(lexer);
        lexer->mark_end(lexer);
        s->depth--;
        lexer->result_symbol = FSTRING_END;
        return true;
      }
      int count = 0;
      while (count < 3 && lexer->lookahead == quote) {
        advance(lexer);
        count++;
      }
      if (count == 3) {
        if (any) return true;
        if (!valid[FSTRING_END]) return false;
        lexer->mark_end(lexer);
        s->depth--;
        lexer->result_symbol = FSTRING_END;
        return true;
      }
      lexer->mark_end(lexer);  // one or two quotes inside a triple f-text
      any = true;
      continue;
    }
    if (c == '{' || c == '}') {
      advance(lexer);
      if (lexer->lookahead == c) {  // {{ and }} are literal braces
        advance(lexer);
        lexer->mark_end(lexer);
        any = true;
        continue;
      }
      return any;  // a hole's '{' (or a stray '}') belongs to the grammar
    }
    if (c == '\n' && !triple) return any;
    if (c == '\\' && !raw) {
      advance(lexer);
      if (lexer->lookahead != '\n' && !lexer->eof(lexer)) advance(lexer);
      lexer->mark_end(lexer);
      any = true;
      continue;
    }
    advance(lexer);
    lexer->mark_end(lexer);
    any = true;
  }
}

// A text literal at the lexer: plain/raw/bytes text as one STRING token, or
// the opening of an f-text (FSTRING_START, pushed on the stack).
static bool scan_string(Scanner *s, TSLexer *lexer, const bool *valid) {
  bool f = false, r = false, b = false;
  for (int n = 0; n < 2; n++) {
    int32_t c = lexer->lookahead;
    if (c == 'f' || c == 'F') {
      if (f) return false;
      f = true;
    } else if (c == 'r' || c == 'R') {
      if (r) return false;
      r = true;
    } else if (c == 'b' || c == 'B') {
      if (b) return false;
      b = true;
    } else {
      break;
    }
    advance(lexer);
  }
  if (f && b) return false;
  int32_t quote = lexer->lookahead;
  if (quote != '"' && quote != '\'') return false;
  if (f ? !valid[FSTRING_START] : !valid[STRING]) return false;
  advance(lexer);
  lexer->mark_end(lexer);
  bool triple = false;
  if (lexer->lookahead == quote) {
    advance(lexer);
    if (lexer->lookahead == quote) {
      advance(lexer);
      lexer->mark_end(lexer);
      triple = true;
    } else if (!f) {
      lexer->mark_end(lexer);  // "" is an empty text
      lexer->result_symbol = STRING;
      return true;
    }
  }
  if (f) {
    if (s->depth >= MAX_DEPTH) return false;
    s->stack[s->depth++] = (uint8_t)quote | (triple ? TRIPLE : 0) | (r ? RAW : 0);
    lexer->result_symbol = FSTRING_START;
    return true;
  }
  for (;;) {
    if (lexer->eof(lexer)) return false;
    int32_t c = lexer->lookahead;
    if (c == quote) {
      advance(lexer);
      if (!triple) break;
      if (lexer->lookahead == quote) {
        advance(lexer);
        if (lexer->lookahead == quote) {
          advance(lexer);
          break;
        }
      }
      continue;
    }
    if (c == '\n' && !triple) return false;
    if (c == '\\' && !r) {
      advance(lexer);
      if (lexer->lookahead != '\n' && !lexer->eof(lexer)) advance(lexer);
      continue;
    }
    advance(lexer);
  }
  lexer->mark_end(lexer);
  lexer->result_symbol = STRING;
  return true;
}

bool tree_sitter_tb_external_scanner_scan(void *payload, TSLexer *lexer, const bool *valid) {
  Scanner *s = (Scanner *)payload;
  if (valid[ERROR_SENTINEL]) return false;  // error recovery: let the grammar's lexer try

  if ((valid[STRING_CONTENT] || valid[FSTRING_END]) && s->depth > 0) {
    return scan_fstring_body(s, lexer, valid);
  }

  if (valid[FORMAT_SPEC]) {
    bool any = false;
    while (!lexer->eof(lexer) && lexer->lookahead != '}' && lexer->lookahead != '\n') {
      advance(lexer);
      any = true;
    }
    lexer->result_symbol = FORMAT_SPEC;
    return any;
  }

  if (valid[CALL_LPAREN] || valid[INDEX_LBRACKET]) {
    while (lexer->lookahead == ' ' || lexer->lookahead == '\t' || lexer->lookahead == '\r' ||
           lexer->lookahead == '\f') {
      skip(lexer);
    }
    if (valid[CALL_LPAREN] && lexer->lookahead == '(') {
      advance(lexer);
      lexer->result_symbol = CALL_LPAREN;
      return true;
    }
    if (valid[INDEX_LBRACKET] && lexer->lookahead == '[') {
      advance(lexer);
      lexer->result_symbol = INDEX_LBRACKET;
      return true;
    }
  }

  bool newline = false;
  while (iswspace(lexer->lookahead)) {
    if (lexer->lookahead == '\n') newline = true;
    skip(lexer);
  }

  if (valid[ARM_END] && newline) {
    lexer->mark_end(lexer);  // zero width: the '-' or 'is' stays for the next arm
    lexer->result_symbol = ARM_END;
    if (lexer->lookahead == '-') return true;
    if (lexer->lookahead == 'i') {
      advance(lexer);
      if (lexer->lookahead != 's') return false;
      advance(lexer);
      return !(iswalnum(lexer->lookahead) || lexer->lookahead == '_');
    }
  }

  if (valid[STRING] || valid[FSTRING_START]) return scan_string(s, lexer, valid);
  return false;
}
