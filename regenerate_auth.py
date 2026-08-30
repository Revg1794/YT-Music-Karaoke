"""Regenerate browser.json from a 'Copy as cURL (bash)' header export plus a
copy of the request's Cookies table (Chrome deliberately omits Cookie from
every code-export format -- a privacy feature -- so it has to come from the
dedicated Cookies tab in the request panel instead).

Usage:
  1. In Chrome/Edge DevTools, Network tab, right-click the `browse` request
     -> Copy -> Copy as cURL (bash). Paste into curl_headers.txt.
  2. In that same request's detail panel, click the "Cookies" tab. Click the
     first row, Shift+click the last row, Ctrl+C. Paste into cookies.txt.
  3. Run: .venv\\Scripts\\python.exe regenerate_auth.py curl_headers.txt cookies.txt
  4. Delete curl_headers.txt and cookies.txt afterwards -- no longer needed
     once browser.json is written.
"""

import re
import sys
from pathlib import Path

from ytmusicapi import setup

HEADER_ARG_RE = re.compile(r"-H\s+'((?:[^'\\]|\\.)*)'")
PSEUDO_HEADERS = {"authority", "method", "path", "scheme"}


def parse_curl_headers(text: str) -> list[str]:
    lines = []
    for match in HEADER_ARG_RE.finditer(text):
        raw = match.group(1).replace("\\'", "'")
        if ":" not in raw:
            continue
        name, value = raw.split(":", 1)
        name = name.strip()
        value = value.strip()
        if name.lstrip(":").lower() in PSEUDO_HEADERS:
            continue
        lines.append(f"{name}: {value}")
    return lines


def parse_cookie_table(text: str) -> str:
    pairs = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        fields = re.split(r"\t+", raw_line.strip())
        if len(fields) < 2:
            fields = re.split(r" {2,}", raw_line.strip())
        if len(fields) < 2:
            continue
        name, value = fields[0].strip(), fields[1].strip()
        if name.lower() == "name" and value.lower() == "value":
            continue  # header row
        if not name or not value:
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python regenerate_auth.py <curl-headers.txt> <cookies.txt>")
        sys.exit(1)

    headers_src = Path(sys.argv[1])
    cookies_src = Path(sys.argv[2])

    lines = parse_curl_headers(headers_src.read_text(encoding="utf-8"))

    cookie_header = parse_cookie_table(cookies_src.read_text(encoding="utf-8"))
    if not cookie_header:
        print(f"Couldn't parse any cookies out of {cookies_src}.")
        sys.exit(1)
    print(f"Parsed {cookie_header.count('=')} cookies from {cookies_src}")
    lines.append(f"Cookie: {cookie_header}")

    headers_raw = "\n".join(lines)
    parsed_names = [line.split(":", 1)[0] for line in lines]
    print(f"Parsed {len(parsed_names)} headers total: {parsed_names}")

    out_path = Path(__file__).parent / "browser.json"
    setup(filepath=str(out_path), headers_raw=headers_raw)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
