#!/usr/bin/env python3
"""
Markdown Corpus Collection Script: Task 4 (RAG Graduation Assessment)
Location: scripts/collect_markdown_corpus.py

Purpose:
Downloads official PostgreSQL 16 HTML documentation pages, extracts the core
documentation content while stripping navigation bars/footers, preserves heading
hierarchies and code fences, prepends standardized provenance frontmatter,
and writes clean source Markdown files to data/raw/markdown/.
"""

import os
import sys
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from bs4 import BeautifulSoup, NavigableString, Tag

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "markdown")

DOC_PAGES = [
    {
        "id": "mvcc-intro",
        "topic": "Transactions & Concurrency",
        "url": "https://www.postgresql.org/docs/16/mvcc-intro.html",
        "filename": "mvcc-intro.md"
    },
    {
        "id": "transaction-iso",
        "topic": "Transactions & Concurrency",
        "url": "https://www.postgresql.org/docs/16/transaction-iso.html",
        "filename": "transaction-iso.md"
    },
    {
        "id": "explicit-locking",
        "topic": "Concurrency & Locking",
        "url": "https://www.postgresql.org/docs/16/explicit-locking.html",
        "filename": "explicit-locking.md"
    },
    {
        "id": "using-explain",
        "topic": "Query Optimization",
        "url": "https://www.postgresql.org/docs/16/using-explain.html",
        "filename": "using-explain.md"
    },
    {
        "id": "routine-vacuuming",
        "topic": "Maintenance & Vacuuming",
        "url": "https://www.postgresql.org/docs/16/routine-vacuuming.html",
        "filename": "routine-vacuuming.md"
    },
    {
        "id": "ddl-partitioning",
        "topic": "Table Partitioning",
        "url": "https://www.postgresql.org/docs/16/ddl-partitioning.html",
        "filename": "ddl-partitioning.md"
    },
    {
        "id": "indexes-types",
        "topic": "Index Types (B-Tree, GIN, BRIN, GiST)",
        "url": "https://www.postgresql.org/docs/16/indexes-types.html",
        "filename": "indexes-types.md"
    },
    {
        "id": "indexes-partial",
        "topic": "Partial Indexes",
        "url": "https://www.postgresql.org/docs/16/indexes-partial.html",
        "filename": "indexes-partial.md"
    },
    {
        "id": "wal-intro",
        "topic": "Write-Ahead Logging",
        "url": "https://www.postgresql.org/docs/16/wal-intro.html",
        "filename": "wal-intro.md"
    },
    {
        "id": "textsearch-intro",
        "topic": "Full-Text Search",
        "url": "https://www.postgresql.org/docs/16/textsearch-intro.html",
        "filename": "textsearch-intro.md"
    },
    {
        "id": "postgres-fdw",
        "topic": "Foreign Data Wrappers",
        "url": "https://www.postgresql.org/docs/16/postgres-fdw.html",
        "filename": "postgres-fdw.md"
    },
    {
        "id": "runtime-config-resource",
        "topic": "Server Configuration",
        "url": "https://www.postgresql.org/docs/16/runtime-config-resource.html",
        "filename": "runtime-config-resource.md"
    },
    {
        "id": "backup-dump",
        "topic": "Backup & Restore",
        "url": "https://www.postgresql.org/docs/16/backup-dump.html",
        "filename": "backup-dump.md"
    },
    {
        "id": "auth-pg-hba-conf",
        "topic": "Client Authentication & Security",
        "url": "https://www.postgresql.org/docs/16/auth-pg-hba-conf.html",
        "filename": "auth-pg-hba-conf.md"
    }
]


def clean_text(text: str) -> str:
    """Normalize whitespace within inline text fragments."""
    return re.sub(r"[ \t]+", " ", text)


def tag_to_markdown(elem) -> str:
    """Recursively converts an HTML tag to clean Markdown."""
    if isinstance(elem, NavigableString):
        return elem.text

    if not isinstance(elem, Tag):
        return ""

    name = elem.name.lower()

    # Headings
    if name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        level = int(name[1])
        prefix = "#" * level
        header_text = elem.get_text(" ", strip=True)
        header_text = header_text.replace("\xa0", " ")
        return f"\n\n{prefix} {header_text}\n\n"

    # Code listings / Preformatted blocks
    if name == "pre":
        classes = elem.get("class", [])
        lang = "sql" if any(c in ["programlisting", "synopsis"] for c in classes) else "text"
        code_text = elem.get_text()
        return f"\n\n```{lang}\n{code_text.strip()}\n```\n\n"

    # Paragraphs
    if name == "p":
        inner = "".join(tag_to_markdown(child) for child in elem.children)
        inner = inner.strip().replace("\xa0", " ")
        return f"\n\n{inner}\n\n"

    # Inline elements
    if name == "code" or name == "tt":
        return f"`{elem.get_text().strip()}`"
    if name in ["strong", "b"]:
        return f"**{elem.get_text().strip()}**"
    if name in ["em", "i"]:
        return f"*{elem.get_text().strip()}*"
    if name == "a":
        link_text = elem.get_text().strip()
        href = elem.get("href", "")
        if href and not href.startswith("#"):
            return f"[{link_text}]({href})"
        return link_text

    # Lists
    if name == "ul":
        items = []
        for li in elem.find_all("li", recursive=False):
            li_content = "".join(tag_to_markdown(c) for c in li.children).strip()
            items.append(f"- {li_content}")
        return "\n\n" + "\n".join(items) + "\n\n"

    if name == "ol":
        items = []
        for i, li in enumerate(elem.find_all("li", recursive=False), 1):
            li_content = "".join(tag_to_markdown(c) for c in li.children).strip()
            items.append(f"{i}. {li_content}")
        return "\n\n" + "\n".join(items) + "\n\n"

    # Tables
    if name == "table":
        rows = elem.find_all("tr")
        if not rows:
            return ""
        table_md = ["\n"]
        headers_found = False
        for row in rows:
            ths = row.find_all("th")
            tds = row.find_all("td")
            if ths and not headers_found:
                col_texts = [th.get_text(" ", strip=True).replace("|", "\\|") for th in ths]
                table_md.append("| " + " | ".join(col_texts) + " |")
                table_md.append("| " + " | ".join(["---"] * len(col_texts)) + " |")
                headers_found = True
            elif tds:
                col_texts = [td.get_text(" ", strip=True).replace("|", "\\|") for td in tds]
                if not headers_found:
                    table_md.append("| " + " | ".join([f"Col {idx+1}" for idx in range(len(col_texts))]) + " |")
                    table_md.append("| " + " | ".join(["---"] * len(col_texts)) + " |")
                    headers_found = True
                table_md.append("| " + " | ".join(col_texts) + " |")
        table_md.append("\n")
        return "\n".join(table_md)

    # Generic container traversal (div, span, section, dl, dt, dd)
    return "".join(tag_to_markdown(child) for child in elem.children)


def extract_page_markdown(html_content: str, meta: dict) -> str:
    soup = BeautifulSoup(html_content, "html.parser")

    # Extract clean title from <title> or top header
    title_tag = soup.find("title")
    page_title = title_tag.get_text().strip() if title_tag else meta["id"]
    page_title = page_title.replace("PostgreSQL: Documentation: 16: ", "").replace("\xa0", " ")

    # Locate main documentation content container
    doc_content = soup.find("div", id="docContent")
    if not doc_content:
        doc_content = soup.find("div", class_="chapter") or soup.find("div", class_="sect1") or soup.body

    # Remove navigation headers, footers, TOCs, and scripts
    for selector in ["div.navheader", "div.navfooter", "div.toc", "hr", "script", "form#docSearchForm"]:
        for unwanted in doc_content.select(selector):
            unwanted.decompose()

    # Convert DOM to markdown text
    raw_md = tag_to_markdown(doc_content)

    # Normalize excessive newlines
    normalized_md = re.sub(r"\n{3,}", "\n\n", raw_md).strip()

    # Build standardized YAML frontmatter
    retrieval_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    frontmatter = (
        f"---\n"
        f"document_id: \"{meta['id']}\"\n"
        f"title: \"{page_title}\"\n"
        f"topic: \"{meta['topic']}\"\n"
        f"source_url: \"{meta['url']}\"\n"
        f"retrieval_date: \"{retrieval_date}\"\n"
        f"corpus_version: \"PostgreSQL 16\"\n"
        f"license: \"PostgreSQL License (OSI-approved, BSD-style permissive)\"\n"
        f"---\n\n"
    )

    return frontmatter + normalized_md + "\n"


def collect_markdown_corpus():
    os.makedirs(TARGET_DIR, exist_ok=True)
    print("=" * 70)
    print("TASK 4 — OFFICIAL POSTGRESQL 16 MARKDOWN CORPUS COLLECTION")
    print("=" * 70)
    print(f"Target Directory: {TARGET_DIR}")
    print(f"Total Pages to Collect: {len(DOC_PAGES)}")
    print("-" * 70)

    # Clean old files if present
    for old_file in ["brin-indexes.md", "gin-indexes.md"]:
        old_path = os.path.join(TARGET_DIR, old_file)
        if os.path.exists(old_path):
            os.remove(old_path)

    success_count = 0
    headers = {"User-Agent": "PostgreSQL-RAG-Project-Markdown-Collector/1.0"}

    for idx, page in enumerate(DOC_PAGES, 1):
        target_path = os.path.join(TARGET_DIR, page["filename"])
        print(f"[{idx}/{len(DOC_PAGES)}] Fetching {page['id']} ({page['topic']})...")
        print(f"    Source: {page['url']}")

        try:
            req = urllib.request.Request(page["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status != 200:
                    print(f"    FAIL: HTTP status {resp.status}", file=sys.stderr)
                    continue
                html = resp.read().decode("utf-8", errors="replace")

            markdown = extract_page_markdown(html, page)

            line_count = len(markdown.splitlines())
            char_count = len(markdown)
            if char_count < 500:
                print(f"    FAIL: Content too short ({char_count} chars). Malformed extraction.", file=sys.stderr)
                continue

            with open(target_path, "w", encoding="utf-8") as f:
                f.write(markdown)

            print(f"    Saved: {target_path} ({char_count:,} chars, {line_count:,} lines)")
            success_count += 1

        except Exception as e:
            print(f"    FAIL: Network or parsing error for {page['url']}: {e}", file=sys.stderr)

    print("-" * 70)
    print(f"Collection complete: {success_count}/{len(DOC_PAGES)} documents successfully processed.")
    print("=" * 70)

    if success_count < 10:
        print(f"ERROR: Collected {success_count} documents, which is below the minimum target of 10.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    collect_markdown_corpus()
