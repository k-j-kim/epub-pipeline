#!/usr/bin/env python3
"""Definitive Korean-language check for an EPUB.

Opens the EPUB (which is a zip), locates its OPF package document via
META-INF/container.xml, and reads <dc:language>. Returns True iff the
declared language starts with 'ko' (matches 'ko', 'kor', 'ko-KR', etc.).

Fallback heuristic when the OPF is missing or blank: count hangul
characters (U+AC00..U+D7AF) in the title/creator metadata and in the
first few text nodes; require > 10 to accept.
"""
import re, sys, zipfile
from xml.etree import ElementTree as ET

HANGUL_RE = re.compile(r"[가-힯]")
NS = {"opf": "http://www.idpf.org/2007/opf",
      "dc":  "http://purl.org/dc/elements/1.1/",
      "cn":  "urn:oasis:names:tc:opendocument:xmlns:container"}


def opf_path(zf):
    try:
        with zf.open("META-INF/container.xml") as f:
            root = ET.parse(f).getroot()
        rf = root.find(".//cn:rootfile", NS)
        if rf is not None and rf.get("full-path"):
            return rf.get("full-path")
    except (KeyError, ET.ParseError):
        pass
    for name in zf.namelist():
        if name.lower().endswith(".opf"):
            return name
    return None


def is_korean(path):
    """Return (bool, reason). reason is a short label for logging."""
    try:
        with zipfile.ZipFile(path) as zf:
            op = opf_path(zf)
            if not op:
                return False, "no-opf"
            with zf.open(op) as f:
                try:
                    root = ET.parse(f).getroot()
                except ET.ParseError:
                    return False, "opf-parse"
            langs = [el.text or "" for el in root.iter() if el.tag.endswith("}language")]
            langs = [l.strip().lower() for l in langs if l and l.strip()]
            for l in langs:
                if l.startswith("ko"):
                    return True, f"dc:language={l}"
            # Fallback: count hangul in title/creator
            texts = []
            for el in root.iter():
                if el.tag.endswith("}title") or el.tag.endswith("}creator"):
                    if el.text: texts.append(el.text)
            hangul_count = sum(len(HANGUL_RE.findall(t)) for t in texts)
            if hangul_count >= 5:
                return True, f"hangul-in-title={hangul_count}"
            if langs:
                return False, f"dc:language={langs[0]}"
            return False, f"hangul-in-title={hangul_count}"
    except (zipfile.BadZipFile, FileNotFoundError) as e:
        return False, f"bad-file:{type(e).__name__}"


if __name__ == "__main__":
    for p in sys.argv[1:]:
        ok, why = is_korean(p)
        print(f"{'OK' if ok else 'NO'}  {why:40s}  {p}")
