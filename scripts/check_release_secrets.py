"""Fail CI when tracked source contains high-confidence credential material."""

import pathlib
import re
import subprocess
import sys


RULES = {
    "generic sk token": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def tracked_files():
    # Include untracked-but-not-ignored files so the local pre-release gate
    # also scans files that are about to be committed for the first time.
    output = subprocess.check_output([
        "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z",
    ])
    return [pathlib.Path(value.decode("utf-8")) for value in output.split(b"\0") if value]


def artifact_files(root):
    root = pathlib.Path(root)
    return [path for path in root.rglob("*") if path.is_file()]


def main():
    findings = []
    paths = artifact_files(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--path" else tracked_files()
    for path in paths:
        try:
            if not path.is_file():
                continue
            # Source scanning skips large generated assets; artifact scanning
            # also checks packaged binaries because PyInstaller can embed data.
            if not (len(sys.argv) > 2 and sys.argv[1] == "--path") and path.stat().st_size > 2 * 1024 * 1024:
                continue
            content = path.read_bytes()
        except OSError:
            continue
        if b"\0" in content[:4096]:
            continue
        for name, pattern in RULES.items():
            for match in pattern.finditer(content):
                line = content.count(b"\n", 0, match.start()) + 1
                findings.append((str(path), line, name))
    if findings:
        for path, line, name in findings:
            print("potential secret: %s:%s (%s)" % (path, line, name))
        return 1
    print("release-secret-scan-ok" if len(sys.argv) > 2 else "tracked-secret-scan-ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
