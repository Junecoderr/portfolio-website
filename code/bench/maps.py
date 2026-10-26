def churn(n):
    m = {}
    for i in range(n):
        k = f"k{i % 1000}"
        if k in m:
            m[k] = m[k] + 1
        else:
            m[k] = 1
        if i % 3 == 0:
            m.pop(k, None)
    return len(m)
print(churn(300000))
