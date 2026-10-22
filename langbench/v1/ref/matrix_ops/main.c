#include <stdio.h>

static void print_matrix(int m[3][3]) {
    for (int i = 0; i < 3; i++) {
        printf("  ");
        for (int j = 0; j < 3; j++) {
            if (j > 0) printf(" ");
            printf("%4d", m[i][j]);
        }
        printf("\n");
    }
}

int main(void) {
    int A[3][3] = {{1, 2, 3}, {4, 5, 6}, {7, 8, 9}};
    int B[3][3] = {{9, 8, 7}, {6, 5, 4}, {3, 2, 1}};
    int C[3][3] = {{0}};

    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++)
            for (int k = 0; k < 3; k++)
                C[i][j] += A[i][k] * B[k][j];

    long det = (long)C[0][0] * (C[1][1] * C[2][2] - C[1][2] * C[2][1])
             - (long)C[0][1] * (C[1][0] * C[2][2] - C[1][2] * C[2][0])
             + (long)C[0][2] * (C[1][0] * C[2][1] - C[1][1] * C[2][0]);

    printf("=== Matrix Operations ===\n\n");
    printf("Matrix A:\n");
    print_matrix(A);
    printf("\n");
    printf("Matrix B:\n");
    print_matrix(B);
    printf("\n");
    printf("A x B =\n");
    print_matrix(C);
    printf("\n");
    printf("Determinant: %ld\n", det);

    return 0;
}
