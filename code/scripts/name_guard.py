"""Keep the author's name off the website without writing it down anywhere.

TB does not credit any person, so the author stays anonymous. This module
knows the name only as SHA-256 digests of its two lowercase parts, and checks
every 4- and 6-letter run of letters in a text against them.
"""

from __future__ import annotations

import hashlib
import re

# sha256 of the two lowercase name parts (4 and 6 letters long).
_DIGESTS = {
    4: "bae6ac56c3cac5372635b835e8b8b3060cb791e346bc632d6d4e1ae8e55c567b",
    6: "8d474150810d37eae59f28ec28272859ed65e2f69232ef0599aaca93626087b8",
}


def contains_name(text: str) -> bool:
    """True if any run of letters in text contains one of the name parts."""
    seen: set[str] = set()
    for word in re.findall(r"[a-z]+", text.lower()):
        for size, digest in _DIGESTS.items():
            for i in range(len(word) - size + 1):
                part = word[i:i + size]
                key = f"{size}:{part}"
                if key in seen:
                    continue
                seen.add(key)
                if hashlib.sha256(part.encode()).hexdigest() == digest:
                    return True
    return False
