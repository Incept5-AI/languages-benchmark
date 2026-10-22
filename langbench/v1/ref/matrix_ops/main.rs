fn print_matrix(m: &[[i64; 3]; 3]) {
    for row in m.iter() {
        let mut line = String::from("  ");
        for (j, v) in row.iter().enumerate() {
            if j > 0 {
                line.push(' ');
            }
            line.push_str(&format!("{:4}", v));
        }
        println!("{}", line);
    }
}

fn main() {
    let a: [[i64; 3]; 3] = [[1, 2, 3], [4, 5, 6], [7, 8, 9]];
    let b: [[i64; 3]; 3] = [[9, 8, 7], [6, 5, 4], [3, 2, 1]];
    let mut c: [[i64; 3]; 3] = [[0; 3]; 3];

    for i in 0..3 {
        for j in 0..3 {
            for k in 0..3 {
                c[i][j] += a[i][k] * b[k][j];
            }
        }
    }

    let det = c[0][0] * (c[1][1] * c[2][2] - c[1][2] * c[2][1])
        - c[0][1] * (c[1][0] * c[2][2] - c[1][2] * c[2][0])
        + c[0][2] * (c[1][0] * c[2][1] - c[1][1] * c[2][0]);

    println!("=== Matrix Operations ===");
    println!();
    println!("Matrix A:");
    print_matrix(&a);
    println!();
    println!("Matrix B:");
    print_matrix(&b);
    println!();
    println!("A x B =");
    print_matrix(&c);
    println!();
    println!("Determinant: {}", det);
}
