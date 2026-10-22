import java.io.*;

public class Main {
    static String run(String... cmd) throws Exception {
        Process pr = new ProcessBuilder(cmd).redirectErrorStream(true).start();
        String out = new String(pr.getInputStream().readAllBytes());
        pr.waitFor();
        return out.strip();
    }
    static String q(String v) { return "\"" + v.replace("\\", "\\\\").replace("\"", "\\\"") + "\""; }
    public static void main(String[] args) throws Exception {
        String os = run("uname", "-s"), arch = run("uname", "-m"), user = run("whoami"), host = run("hostname");
        System.out.println("{\n  \"hostname\": " + q(host) + ",\n  \"os\": " + q(os) + ",\n  \"architecture\": " + q(arch) + ",\n  \"username\": " + q(user) + "\n}");
    }
}
