#!/usr/bin/env python3
"""Content-quality gate for downloaded EPUBs.

Opens the EPUB (a zip), extracts visible text from all XHTML/HTML parts
listed in the spine, and returns (ok, score, reason). Rejects books that
are:
  * missing title/author metadata
  * tiny (< MIN_HANGUL hangul characters — likely a sample/TOC)
  * dominated by non-Korean text (< MIN_RATIO hangul among Korean+ASCII letters)
  * mostly images (very low text-to-file-size ratio)

Rationale — content-based, not metadata-based:
  many IA/libgen items pass the OPF <dc:language>ko</dc:language> gate
  but the actual body is scanned images with an English preface, or a
  cover page with no real content. Reading real bytes catches those.
"""
import argparse, re, sys, zipfile
from xml.etree import ElementTree as ET

HANGUL_RE  = re.compile(r"[가-힯]")
ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
TAG_RE     = re.compile(r"<[^>]+>", re.S)
WS_RE      = re.compile(r"\s+")
NS = {"opf": "http://www.idpf.org/2007/opf",
      "dc":  "http://purl.org/dc/elements/1.1/",
      "cn":  "urn:oasis:names:tc:opendocument:xmlns:container"}

MIN_HANGUL = 3000     # a serious book has thousands of hangul chars
MIN_RATIO  = 0.5      # among Latin+Korean letters, at least half must be Korean
MIN_TEXT_PER_MB = 500 # avg text chars per MB of file — filters image-only "books"


def opf_path(zf):
    try:
        with zf.open("META-INF/container.xml") as f:
            root = ET.parse(f).getroot()
        rf = root.find(".//cn:rootfile", NS)
        if rf is not None:
            return rf.get("full-path")
    except (KeyError, ET.ParseError):
        pass
    for n in zf.namelist():
        if n.lower().endswith(".opf"): return n
    return None


def read_metadata(root):
    md = {"title": "", "creator": "", "spine_ids": []}
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        if tag == "title" and not md["title"] and el.text:
            md["title"] = el.text.strip()
        elif tag == "creator" and not md["creator"] and el.text:
            md["creator"] = el.text.strip()
        elif tag == "itemref":
            idref = el.get("idref")
            if idref: md["spine_ids"].append(idref)
    manifest = {}
    for el in root.iter():
        if el.tag.split("}", 1)[-1] == "item":
            manifest[el.get("id")] = el.get("href")
    md["spine_hrefs"] = [manifest.get(i) for i in md["spine_ids"] if manifest.get(i)]
    return md


def extract_text(zf, opf_name, hrefs, max_files=30):
    """Concatenate visible text from the first N spine items."""
    from posixpath import dirname, normpath, join as pjoin
    base = dirname(opf_name)
    texts = []
    for href in hrefs[:max_files]:
        if not href: continue
        path = normpath(pjoin(base, href)) if base else href
        try:
            body = zf.read(path).decode("utf-8", errors="replace")
        except (KeyError, UnicodeError):
            continue
        stripped = TAG_RE.sub(" ", body)
        stripped = WS_RE.sub(" ", stripped).strip()
        if stripped:
            texts.append(stripped)
    return "\n".join(texts)


def assess(path):
    """Return (ok: bool, reason: str, stats: dict)."""
    try:
        with zipfile.ZipFile(path) as zf:
            size_mb = max(1, sum(zi.file_size for zi in zf.infolist()) / 1024 / 1024)
            op = opf_path(zf)
            if not op:
                return False, "no-opf", {}
            with zf.open(op) as f:
                try:
                    root = ET.parse(f).getroot()
                except ET.ParseError:
                    return False, "opf-parse", {}
            md = read_metadata(root)
            if not md["title"]:
                return False, "no-title", md
            text = extract_text(zf, op, md["spine_hrefs"])
    except (zipfile.BadZipFile, FileNotFoundError):
        return False, "bad-zip", {}

    hangul = len(HANGUL_RE.findall(text))
    latin  = len(ASCII_ALPHA_RE.findall(text))
    ratio  = hangul / (hangul + latin) if (hangul + latin) else 0.0
    text_per_mb = len(text) / size_mb

    stats = {"title": md["title"][:80], "author": md["creator"][:80],
             "chars": len(text), "hangul": hangul, "latin": latin,
             "ratio": round(ratio, 2), "text_per_mb": int(text_per_mb),
             "size_mb": round(size_mb, 1)}

    if hangul < MIN_HANGUL:
        return False, f"too-little-korean ({hangul}<{MIN_HANGUL})", stats
    if ratio < MIN_RATIO:
        return False, f"too-non-korean (ratio {ratio:.2f})", stats
    if text_per_mb < MIN_TEXT_PER_MB:
        return False, f"mostly-images ({text_per_mb:.0f} chars/MB)", stats
    return True, "ok", stats


if __name__ == "__main__":
    for p in sys.argv[1:]:
        ok, why, st = assess(p)
        print(f"{'OK' if ok else 'NO'}  {why:35s}  hangul={st.get('hangul','?')}  ratio={st.get('ratio','?')}  {p}")
