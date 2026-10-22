A = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
B = [[9, 8, 7], [6, 5, 4], [3, 2, 1]]
n = 3
C = [[sum(A[i][k] * B[k][j] for k in range(n)) for j in range(n)] for i in range(n)]

def det(m):
    if len(m) == 1:
        return m[0][0]
    return sum((-1) ** j * m[0][j] * det([r[:j] + r[j + 1:] for r in m[1:]]) for j in range(len(m)))

def show(m):
    for row in m:
        print("  " + " ".join(f"{v:4d}" for v in row))

print("=== Matrix Operations ===")
print()
print("Matrix A:")
show(A)
print()
print("Matrix B:")
show(B)
print()
print("A x B =")
show(C)
print()
print(f"Determinant: {det(C)}")
