class Counter:
    def __init__(self):
        self.count = 0
    def add(self, n):
        self.count = self.count + n
    def get(self):
        return self.count
class Point:
    def __init__(self, x, y):
        self.x = x; self.y = y
    def dot(self, other):
        return self.x * other.x + self.y * other.y
c = Counter(); p = Point(1, 2); q = Point(3, 4)
i = 0
while i < 300000:
    c.add(p.dot(q))
    i += 1
print(c.get())
