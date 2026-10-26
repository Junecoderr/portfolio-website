def evens(n):
    i = 0
    while i < n:
        if i % 2 == 0:
            yield i
        i += 1
total = 0
for x in evens(600000):
    total += x
print(total)
