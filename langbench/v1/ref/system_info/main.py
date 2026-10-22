import json
import subprocess

def run(*c):
    return subprocess.run(c, capture_output=True, text=True).stdout.strip()

print(json.dumps({
    "hostname": run("hostname"),
    "os": run("uname", "-s"),
    "architecture": run("uname", "-m"),
    "username": run("whoami"),
}, indent=2))
