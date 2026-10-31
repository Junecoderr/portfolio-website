# Hash files

Hash every file of the evidence, so you can prove later that nobody changed it. Look up the hashes in threat intelligence, or compare them with a list of known bad files.

## Recipe

```tb
import fs
import hash

let folder = "apps/data/samples"
for name in fs.list(folder).sort() {
  let path = fs.join(folder, name)
  let h = hash.file(path)
  say(f"{name}  {fs.size(path)} bytes  sha256 {h.sha256.slice(0, 16)}...  md5 {h.md5}")
}
```

## Compare with known bad hashes

```tb
import fs
import hash

let known_bad = {hash.file_sha256("apps/data/samples/tiny.exe"): "test dropper"}
for path in fs.walk("apps/data").filter(p => fs.is_file(p)) {
  let name = known_bad.get(hash.file_sha256(path))
  if name {
    say(f"MATCH {path}: {name}")
  }
}
```

## Sign the list

A hash shows that a file changed. A keyed hash (HMAC) also shows who made the list: only a person with the key can make the same value.

```tb
import hash
import crypto

let manifest = "tiny.exe " + hash.file_sha256("apps/data/samples/tiny.exe")
let key = "case-2026-031-key"
let signature = crypto.hmac(key, manifest)
say(manifest.slice(0, 30) + "...")
say(hash.compare(signature, crypto.hmac(key, manifest)))
```

## How it works

- `hash.file` reads the file once and gives `md5`, `sha1`, `sha256` and `sha512`.
- `hash.compare` compares two hashes in constant time. Use it for secrets.
- Keep the HMAC key out of your code, for example in an environment variable.

See also: [hash reference](site:lib/hash.html), [crypto reference](site:lib/crypto.html).
