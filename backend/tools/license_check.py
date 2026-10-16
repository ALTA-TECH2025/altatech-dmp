"""SBOM（SPDX JSON）授權白名單檢查：任何不在白名單的授權 → exit 1。白名單與 LICENSES.md 同步。"""
import json
import sys

ALLOW = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "PostgreSQL", "MPL-2.0", "Python-2.0", "PSF-2.0", "Unlicense", "0BSD",
         "CC0-1.0", "Zlib", "BlueOak-1.0.0", "GPL-2.0-with-classpath-exception"}
EXCEPTIONS = {"renovate", "gitleaks", "semgrep"}   # 僅 CI 使用的工具，不進產品


def main(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        sbom = json.load(f)
    bad = []
    for p in sbom.get("packages", []):
        lic = (p.get("licenseConcluded") or p.get("licenseDeclared") or "NOASSERTION").replace("(", "").replace(")", "")
        parts = [x.strip() for x in lic.replace(" OR ", "|").replace(" AND ", "|").split("|")]
        if lic == "NOASSERTION" or p.get("name", "") in EXCEPTIONS:
            continue
        if not any(x in ALLOW for x in parts):
            bad.append((p.get("name"), p.get("versionInfo"), lic))
    for b in bad:
        print("DENY", *b)
    print(f"checked {len(sbom.get('packages', []))} packages, {len(bad)} denied")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1])
