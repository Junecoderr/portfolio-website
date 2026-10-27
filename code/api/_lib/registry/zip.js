// Reading and checking package archives (zip) on the server, with node:zlib
// only. An archive is refused unless every rule holds: see validateArchive.

import { inflateRawSync } from "node:zlib";

import {
  IMAGE_EXTENSIONS, MAX_DEPENDENCIES, MAX_DESCRIPTION, MAX_FILE_BYTES, MAX_FILES, MAX_KEYWORDS,
  MAX_PATH_LENGTH, MAX_README_BYTES, MAX_TOTAL_BYTES, TEXT_EXTENSIONS, TEXT_NAMES, TomlError, parseToml,
} from "./rules.js";

export class ArchiveError extends Error {}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const utf8 = new TextDecoder("utf-8", { fatal: true });

/**
 * The entries of a zip: [{name, isDir, mode, os, method, size, data()}].
 * Refuses zip64, encrypted entries, multi-disk archives and unknown compression.
 */
export function readZip(bytes) {
  const buf = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const minEocd = Math.max(0, buf.length - 22 - 0xffff);
  let eocd = -1;
  for (let i = buf.length - 22; i >= minEocd; i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new ArchiveError("The upload isn't a zip file.");
  const disk = buf.readUInt16LE(eocd + 4);
  const cdDisk = buf.readUInt16LE(eocd + 6);
  const count = buf.readUInt16LE(eocd + 10);
  const cdSize = buf.readUInt32LE(eocd + 12);
  const cdOffset = buf.readUInt32LE(eocd + 16);
  if (disk !== 0 || cdDisk !== 0) throw new ArchiveError("Multi-part zip files aren't accepted.");
  if (count === 0xffff || cdOffset === 0xffffffff || cdSize === 0xffffffff) throw new ArchiveError("ZIP64 archives aren't accepted.");
  if (cdOffset + cdSize > eocd) throw new ArchiveError("The zip file is damaged (central directory).");
  if (count > MAX_FILES * 2) throw new ArchiveError(`The archive has more than ${MAX_FILES} files.`);
  const entries = [];
  let p = cdOffset;
  for (let i = 0; i < count; i++) {
    if (p + 46 > buf.length || buf.readUInt32LE(p) !== 0x02014b50) throw new ArchiveError("The zip file is damaged (entry header).");
    const madeBy = buf.readUInt16LE(p + 4);
    const flags = buf.readUInt16LE(p + 8);
    const method = buf.readUInt16LE(p + 10);
    const crc = buf.readUInt32LE(p + 16);
    const csize = buf.readUInt32LE(p + 20);
    const usize = buf.readUInt32LE(p + 24);
    const nlen = buf.readUInt16LE(p + 28);
    const xlen = buf.readUInt16LE(p + 30);
    const clen = buf.readUInt16LE(p + 32);
    const ext = buf.readUInt32LE(p + 38);
    const local = buf.readUInt32LE(p + 42);
    let name;
    try {
      name = utf8.decode(buf.subarray(p + 46, p + 46 + nlen));
    } catch {
      throw new ArchiveError("A file name in the archive isn't valid UTF-8.");
    }
    p += 46 + nlen + xlen + clen;
    if (flags & 0x1) throw new ArchiveError("Encrypted zip entries aren't accepted.");
    if (method !== 0 && method !== 8) throw new ArchiveError(`${name}: only stored or deflated files are accepted.`);
    if (csize === 0xffffffff || usize === 0xffffffff || local === 0xffffffff) throw new ArchiveError("ZIP64 archives aren't accepted.");
    const os = madeBy >> 8;
    const mode = os === 3 ? (ext >>> 16) & 0xffff : 0;
    entries.push({
      name, os, mode, method, size: usize, csize, crc, isDir: name.endsWith("/") || (ext & 0x10) !== 0,
      data() {
        if (local + 30 > buf.length || buf.readUInt32LE(local) !== 0x04034b50) throw new ArchiveError(`${name}: damaged entry.`);
        const start = local + 30 + buf.readUInt16LE(local + 26) + buf.readUInt16LE(local + 28);
        if (start + csize > buf.length) throw new ArchiveError(`${name}: damaged entry.`);
        const raw = buf.subarray(start, start + csize);
        let out;
        if (method === 0) {
          out = raw;
        } else {
          try {
            out = inflateRawSync(raw, { maxOutputLength: Math.max(1, usize) });
          } catch {
            throw new ArchiveError(`${name}: couldn't be decompressed (or is bigger than it claims).`);
          }
        }
        if (out.length !== usize || crc32(out) !== crc) throw new ArchiveError(`${name}: damaged (size or checksum).`);
        return out;
      },
    });
  }
  return entries;
}

const S_IFMT = 0o170000;
const S_IFLNK = 0o120000;
const S_IFREG = 0o100000;
const S_IFDIR = 0o040000;

/** Why a path inside an archive is unsafe, or null. */
export function unsafePath(name) {
  if (name.length === 0 || name.length > MAX_PATH_LENGTH) return "is too long or empty";
  if (/[\u0000-\u001f\u007f]/.test(name)) return "has control characters";
  if (name.includes("\\")) return "uses backslashes";
  if (name.startsWith("/") || /^[A-Za-z]:/.test(name)) return "is an absolute path";
  if (/[<>:"|?*]/.test(name)) return "has characters Windows can't use in file names";
  const parts = name.replace(/\/$/, "").split("/");
  for (const part of parts) {
    if (part === "" ) return "has an empty folder name";
    if (part === "." || part === "..") return "has . or .. in it";
    if (part.startsWith(".")) return "is a hidden file (names starting with .)";
    if (/[ .]$/.test(part)) return "ends a name with a space or dot";
  }
  return null;
}

function extension(name) {
  const base = name.split("/").pop().toLowerCase();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot) : "";
}

function isText(name) {
  const base = name.split("/").pop().toLowerCase();
  const ext = extension(name);
  if (TEXT_EXTENSIONS.includes(ext)) return true;
  const stem = ext ? base.slice(0, -ext.length) : base;
  return TEXT_NAMES.includes(stem) && (!ext || ext === ".md" || ext === ".txt");
}

function imageMatches(ext, data) {
  const b = data;
  if (ext === ".png") return b.length > 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47;
  if (ext === ".jpg" || ext === ".jpeg") return b.length > 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff;
  if (ext === ".gif") return b.length > 6 && String.fromCharCode(...b.subarray(0, 4)) === "GIF8";
  if (ext === ".webp") return b.length > 12 && String.fromCharCode(...b.subarray(0, 4)) === "RIFF" && String.fromCharCode(...b.subarray(8, 12)) === "WEBP";
  return false;
}

function cleanText(value, max) {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, "").trim().slice(0, max);
}

/**
 * Check a package archive for `name` `version`. Returns what the index stores
 * about it; throws ArchiveError with a plain-English reason otherwise.
 */
export function validateArchive(bytes, name, version) {
  const entries = readZip(bytes);
  for (const e of entries) {
    const why = unsafePath(e.name);
    if (why) throw new ArchiveError(`The archive path '${e.name.slice(0, 80)}' ${why}.`);
    const type = e.mode & S_IFMT;
    if (type === S_IFLNK) throw new ArchiveError(`'${e.name}' is a symbolic link; links aren't allowed.`);
    if (type && type !== S_IFREG && type !== S_IFDIR) throw new ArchiveError(`'${e.name}' isn't a regular file.`);
  }
  const files = entries.filter((e) => !e.isDir);
  if (!files.length) throw new ArchiveError("The archive is empty.");
  if (files.length > MAX_FILES) throw new ArchiveError(`The archive has ${files.length} files; the limit is ${MAX_FILES}.`);
  // One shared top-level folder (name/ or name-1.0.0/) is stripped, as `tb add` does.
  const heads = new Set(files.map((e) => e.name.split("/")[0]));
  const prefix = heads.size === 1 && files.every((e) => e.name.includes("/")) ? [...heads][0] + "/" : "";
  const seen = new Set();
  const out = new Map();
  let total = 0;
  for (const e of files) {
    const rel = e.name.slice(prefix.length);
    const key = rel.toLowerCase();
    if (seen.has(key)) throw new ArchiveError(`'${rel}' is in the archive twice.`);
    seen.add(key);
    if (rel.split("/")[0] === "tb_packages") throw new ArchiveError("Leave tb_packages/ out of the archive (it holds your dependencies).");
    if (e.size > MAX_FILE_BYTES) throw new ArchiveError(`'${rel}' is bigger than ${MAX_FILE_BYTES / 1024 / 1024} MB.`);
    total += e.size;
    if (total > MAX_TOTAL_BYTES) throw new ArchiveError(`The archive unpacks to more than ${MAX_TOTAL_BYTES / 1024 / 1024} MB.`);
    const ext = extension(rel);
    if (IMAGE_EXTENSIONS.includes(ext)) {
      if (!imageMatches(ext, e.data())) throw new ArchiveError(`'${rel}' isn't the image its name says.`);
      out.set(rel, null);
      continue;
    }
    if (!isText(rel)) {
      throw new ArchiveError(`'${rel}' isn't allowed: packages hold TB code and text files (${TEXT_EXTENSIONS.join(" ")}, LICENSE, README) plus images for the README.`);
    }
    const data = e.data();
    if (data.includes(0)) throw new ArchiveError(`'${rel}' looks like a binary file; only text is allowed.`);
    let text;
    try {
      text = utf8.decode(data);
    } catch {
      throw new ArchiveError(`'${rel}' isn't UTF-8 text.`);
    }
    out.set(rel, text);
  }
  if (!out.has("tb.toml")) throw new ArchiveError("The archive has no tb.toml at the top.");
  let manifest;
  try {
    manifest = parseToml(out.get("tb.toml"));
  } catch (err) {
    if (err instanceof TomlError) throw new ArchiveError(`tb.toml can't be read: ${err.message}.`);
    throw err;
  }
  const pkg = manifest.package;
  if (!pkg || typeof pkg !== "object") throw new ArchiveError("tb.toml has no [package] table.");
  if (pkg.name !== name) throw new ArchiveError(`tb.toml says the name is '${String(pkg.name ?? "")}', but you're publishing '${name}'.`);
  if (String(pkg.version ?? "") !== version) throw new ArchiveError(`tb.toml says the version is '${String(pkg.version ?? "")}', but you're publishing '${version}'.`);
  let entry = null;
  if (pkg.main !== undefined) {
    if (typeof pkg.main !== "string" || !pkg.main.endsWith(".tb") || unsafePath(pkg.main)) throw new ArchiveError("[package] main in tb.toml must be a .tb file inside the package.");
    if (!out.has(pkg.main)) throw new ArchiveError(`tb.toml names main = "${pkg.main}", but that file isn't in the archive.`);
    entry = pkg.main;
  } else {
    entry = ["lib.tb", `${name}.tb`, `${name.replace(/-/g, "_")}.tb`].find((f) => out.has(f)) || null;
  }
  if (!entry) throw new ArchiveError("There's no entry file: add lib.tb (or set [package] main = \"file.tb\").");
  const keywords = Array.isArray(pkg.keywords) ? pkg.keywords : [];
  if (pkg.keywords !== undefined && !Array.isArray(pkg.keywords)) throw new ArchiveError("[package] keywords must be a list of words.");
  if (keywords.length > MAX_KEYWORDS) throw new ArchiveError(`At most ${MAX_KEYWORDS} keywords, please.`);
  for (const k of keywords) {
    if (typeof k !== "string" || !/^[a-z0-9][a-z0-9-]{0,29}$/.test(k)) throw new ArchiveError(`The keyword '${String(k).slice(0, 40)}' isn't a short lower-case word.`);
  }
  if (pkg.description !== undefined && typeof pkg.description !== "string") throw new ArchiveError("[package] description must be text.");
  if (typeof pkg.description === "string" && pkg.description.length > MAX_DESCRIPTION) throw new ArchiveError(`The description is longer than ${MAX_DESCRIPTION} characters.`);
  const deps = manifest.dependencies ?? {};
  if (typeof deps !== "object" || Array.isArray(deps)) throw new ArchiveError("[dependencies] must be a table.");
  const dependencies = {};
  const depNames = Object.keys(deps);
  if (depNames.length > MAX_DEPENDENCIES) throw new ArchiveError(`At most ${MAX_DEPENDENCIES} dependencies.`);
  for (const dep of depNames) {
    const value = deps[dep];
    if (!/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(dep)) throw new ArchiveError(`The dependency name '${dep.slice(0, 40)}' isn't valid.`);
    if (typeof value === "string") dependencies[dep] = cleanText(value, 100);
    else if (value && typeof value === "object" && !Array.isArray(value)) {
      const kept = {};
      for (const k of ["version", "git", "zip", "tag", "path"]) if (typeof value[k] === "string") kept[k] = cleanText(value[k], 300);
      dependencies[dep] = kept;
    } else throw new ArchiveError(`The dependency '${dep}' must be a version range or a table.`);
  }
  let readme = "";
  const readmeName = [...out.keys()].find((f) => /^readme(\.md|\.txt)?$/i.test(f));
  if (readmeName) {
    readme = out.get(readmeName) || "";
    if (Buffer.byteLength(readme) > MAX_README_BYTES) readme = readme.slice(0, MAX_README_BYTES) + "\n\n(README shortened by the index.)\n";
  }
  return {
    entry,
    files: [...out.keys()].sort(),
    fileCount: out.size,
    unpackedSize: total,
    description: cleanText(pkg.description, MAX_DESCRIPTION),
    license: cleanText(pkg.license, 64),
    keywords,
    dependencies,
    readme,
  };
}
