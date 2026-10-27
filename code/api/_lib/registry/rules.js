// The TB Package Index's rules: names, versions, limits, the typosquat check
// and a small TOML reader for tb.toml. The CLI (tbtools/pkg.py) applies the
// same rules before uploading; tests/tools/test_registry_cli.py checks that the
// two lists of reserved words agree.

import { STDLIB_NAMES } from "./seed.js";

export const NAME_RE = /^[a-z][a-z0-9-]{1,38}[a-z0-9]$/;
// Semantic versions without build metadata: 1.2.3 or 1.2.3-beta.1.
export const VERSION_RE = /^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;
export const MAX_VERSION_LENGTH = 64;

// Vercel Functions accept request bodies up to 4.5 MB, so archives stop at 4 MB.
export const MAX_ARCHIVE_BYTES = 4 * 1024 * 1024;
export const MAX_FILES = 200;
export const MAX_FILE_BYTES = 1024 * 1024;
export const MAX_TOTAL_BYTES = 10 * 1024 * 1024;
export const MAX_PATH_LENGTH = 200;
export const MAX_README_BYTES = 100 * 1024;
export const MAX_DESCRIPTION = 300;
export const MAX_KEYWORDS = 10;
export const MAX_DEPENDENCIES = 50;

// Words no package may be called, besides every standard library name.
export const RESERVED_WORDS = [
  "tb", "tbcore", "tblib", "tbtools", "std", "stdlib", "core", "builtin", "builtins",
  "admin", "api", "registry", "packages", "package", "index", "official", "system", "root",
  "null", "none", "nil", "true", "false", "login", "logout", "search", "publish",
];

export const TEXT_EXTENSIONS = [
  ".tb", ".toml", ".md", ".txt", ".json", ".csv", ".tsv", ".yml", ".yaml", ".xml", ".ini", ".cfg", ".log",
];
export const TEXT_NAMES = ["license", "licence", "copying", "notice", "authors", "changelog", "readme", "contributors"];
export const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp"];

export function reservedNames() {
  return new Set([...RESERVED_WORDS, ...STDLIB_NAMES]);
}

export function checkName(name) {
  if (typeof name !== "string" || !NAME_RE.test(name)) {
    return "Package names are 3 to 40 characters: lower-case letters, digits and -, starting with a letter and not ending with -.";
  }
  if (name.includes("--")) return "Package names can't contain two - in a row.";
  if (reservedNames().has(name)) return `'${name}' is reserved (a TB standard library name or a word the index uses).`;
  return null;
}

export function parseVersion(text) {
  if (typeof text !== "string" || text.length > MAX_VERSION_LENGTH) return null;
  const m = VERSION_RE.exec(text);
  if (!m) return null;
  return { major: Number(m[1]), minor: Number(m[2]), patch: Number(m[3]), pre: m[4] ? m[4].split(".") : [] };
}

function compareIds(a, b) {
  const an = /^\d+$/.test(a);
  const bn = /^\d+$/.test(b);
  if (an && bn) return Math.sign(Number(a) - Number(b));
  if (an) return -1;
  if (bn) return 1;
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Semver precedence: -1, 0 or 1. Unparseable versions sort first. */
export function compareVersions(a, b) {
  const x = parseVersion(a);
  const y = parseVersion(b);
  if (!x || !y) return x ? 1 : y ? -1 : 0;
  for (const k of ["major", "minor", "patch"]) {
    if (x[k] !== y[k]) return x[k] < y[k] ? -1 : 1;
  }
  if (!x.pre.length || !y.pre.length) return x.pre.length ? -1 : y.pre.length ? 1 : 0;
  for (let i = 0; i < Math.max(x.pre.length, y.pre.length); i++) {
    if (i >= x.pre.length) return -1;
    if (i >= y.pre.length) return 1;
    const c = compareIds(x.pre[i], y.pre[i]);
    if (c) return c;
  }
  return 0;
}

export function isPrerelease(version) {
  const v = parseVersion(version);
  return Boolean(v && v.pre.length);
}

/** Levenshtein distance, stopping early once it passes `limit`. */
export function editDistance(a, b, limit = 2) {
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost);
      best = Math.min(best, row[j]);
    }
    if (best > limit) return limit + 1;
    prev = row;
  }
  return prev[b.length];
}

/** Existing names that `name` could be mistaken for (edit distance <= 1, or the same without dashes). */
export function similarNames(name, existing) {
  const bare = name.replace(/-/g, "");
  const out = [];
  for (const other of existing) {
    if (other === name) continue;
    if (editDistance(name, other, 1) <= 1 || other.replace(/-/g, "") === bare) out.push(other);
  }
  return out.sort();
}

// ---------------------------------------------------------------- TOML (the tb.toml subset)

export class TomlError extends Error {}

function stripComment(line) {
  let quote = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quote) {
      if (ch === "\\" && quote === '"') { i++; continue; }
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === "#") {
      return line.slice(0, i);
    }
  }
  return line;
}

function splitTop(text, sep) {
  const items = [];
  let buf = "";
  let depth = 0;
  let quote = null;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote) {
      buf += ch;
      if (ch === "\\" && quote === '"' && i + 1 < text.length) { buf += text[++i]; continue; }
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") { quote = ch; buf += ch; }
    else if (ch === "[" || ch === "{") { depth++; buf += ch; }
    else if (ch === "]" || ch === "}") { depth--; buf += ch; }
    else if (ch === sep && depth === 0) { items.push(buf.trim()); buf = ""; }
    else buf += ch;
  }
  if (buf.trim()) items.push(buf.trim());
  return items;
}

function keyPath(text) {
  const parts = [];
  const re = /\s*("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_-]+)\s*(\.|$)/y;
  let pos = 0;
  const src = text.trim();
  while (pos < src.length) {
    re.lastIndex = pos;
    const m = re.exec(src);
    if (!m || m[0].length === 0) throw new TomlError(`bad key: ${text.trim()}`);
    let key = m[1];
    if (key.startsWith('"')) key = parseString(key);
    else if (key.startsWith("'")) key = key.slice(1, -1);
    parts.push(key);
    pos = re.lastIndex;
  }
  if (!parts.length) throw new TomlError("empty key");
  return parts;
}

function parseString(text) {
  if (text.startsWith("'")) return text.slice(1, -1);
  let out = "";
  for (let i = 1; i < text.length - 1; i++) {
    const ch = text[i];
    if (ch !== "\\") { out += ch; continue; }
    const n = text[++i];
    const simple = { b: "\b", t: "\t", n: "\n", f: "\f", r: "\r", '"': '"', "\\": "\\" };
    if (n in simple) out += simple[n];
    else if (n === "u" || n === "U") {
      const len = n === "u" ? 4 : 8;
      const hex = text.slice(i + 1, i + 1 + len);
      if (!/^[0-9A-Fa-f]+$/.test(hex) || hex.length !== len) throw new TomlError("bad escape");
      out += String.fromCodePoint(parseInt(hex, 16));
      i += len;
    } else throw new TomlError("bad escape");
  }
  return out;
}

function parseValue(text) {
  const t = text.trim();
  if (t.startsWith('"""') || t.startsWith("'''")) throw new TomlError("multi-line strings aren't supported in tb.toml");
  if ((t.startsWith('"') && t.endsWith('"') && t.length >= 2) || (t.startsWith("'") && t.endsWith("'") && t.length >= 2)) {
    return parseString(t);
  }
  if (t === "true" || t === "false") return t === "true";
  if (t.startsWith("[")) {
    if (!t.endsWith("]")) throw new TomlError("arrays must be on one line in tb.toml");
    return splitTop(t.slice(1, -1), ",").map(parseValue);
  }
  if (t.startsWith("{")) {
    if (!t.endsWith("}")) throw new TomlError("inline tables must be on one line");
    const table = {};
    for (const item of splitTop(t.slice(1, -1), ",")) {
      const eq = item.indexOf("=");
      if (eq < 0) throw new TomlError(`bad inline table item: ${item}`);
      const path = keyPath(item.slice(0, eq));
      table[path[path.length - 1]] = parseValue(item.slice(eq + 1));
    }
    return table;
  }
  if (/^[+-]?\d[\d_]*$/.test(t)) return Number(t.replace(/_/g, ""));
  if (/^[+-]?\d[\d_]*(\.\d[\d_]*)?([eE][+-]?\d+)?$/.test(t)) return Number(t.replace(/_/g, ""));
  throw new TomlError(`a value I can't read: ${t.slice(0, 40)}`);
}

function walk(root, path) {
  let table = root;
  for (const key of path) {
    if (key === "__proto__" || key === "constructor" || key === "prototype") throw new TomlError("bad key");
    if (!Object.hasOwn(table, key)) table[key] = {};
    table = table[key];
    if (Array.isArray(table)) table = table[table.length - 1];
    if (typeof table !== "object" || table === null) throw new TomlError("a key is both a value and a table");
  }
  return table;
}

/** Parse tb.toml (tables, strings, numbers, booleans, one-line arrays and inline tables). */
export function parseToml(text) {
  const root = {};
  let table = root;
  const lines = String(text).split(/\r?\n/);
  for (let n = 0; n < lines.length; n++) {
    const line = stripComment(lines[n]).trim();
    if (!line) continue;
    try {
      if (line.startsWith("[[") && line.endsWith("]]")) {
        const path = keyPath(line.slice(2, -2));
        const parent = walk(root, path.slice(0, -1));
        const last = path[path.length - 1];
        if (!Array.isArray(parent[last])) parent[last] = [];
        parent[last].push({});
        table = parent[last][parent[last].length - 1];
      } else if (line.startsWith("[") && line.endsWith("]")) {
        table = walk(root, keyPath(line.slice(1, -1)));
      } else {
        const idx = line.indexOf("=");
        if (idx < 0) throw new TomlError("expected key = value");
        const path = keyPath(line.slice(0, idx));
        const target = walk(table, path.slice(0, -1));
        const key = path[path.length - 1];
        if (key === "__proto__" || key === "constructor" || key === "prototype") throw new TomlError("bad key");
        target[key] = parseValue(line.slice(idx + 1));
      }
    } catch (err) {
      if (err instanceof TomlError) throw new TomlError(`line ${n + 1}: ${err.message}`);
      throw err;
    }
  }
  return root;
}
