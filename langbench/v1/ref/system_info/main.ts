import { execSync } from "child_process";

const run = (c: string) => execSync(c, { encoding: "utf8" }).trim();
console.log(JSON.stringify({
  hostname: run("hostname"),
  os: run("uname -s"),
  architecture: run("uname -m"),
  username: run("whoami"),
}, null, 2));
