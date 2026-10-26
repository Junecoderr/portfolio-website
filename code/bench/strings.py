def build(n):
    parts = []
    line = ""
    for i in range(n):
        line = line + "x"
        if len(line) > 40:
            line = ""
        parts.append(f"{i}:{line}")
    return ",".join(parts)
print(len(build(200000)))
