public class Main {
    static void print(long[][] m) {
        for (long[] r : m) {
            StringBuilder sb = new StringBuilder("  ");
            for (int j = 0; j < r.length; j++) { if (j > 0) sb.append(' '); sb.append(String.format("%4d", r[j])); }
            System.out.println(sb);
        }
    }
    static long det(long[][] m) {
        return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
             - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
             + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    }
    public static void main(String[] args) {
        long[][] a = {{1,2,3},{4,5,6},{7,8,9}}, b = {{9,8,7},{6,5,4},{3,2,1}};
        long[][] c = new long[3][3];
        for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) for (int k = 0; k < 3; k++) c[i][j] += a[i][k] * b[k][j];
        System.out.println("=== Matrix Operations ===\n");
        System.out.println("Matrix A:"); print(a);
        System.out.println("\nMatrix B:"); print(b);
        System.out.println("\nA x B ="); print(c);
        System.out.println("\nDeterminant: " + det(c));
    }
}
