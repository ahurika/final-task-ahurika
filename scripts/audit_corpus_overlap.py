import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.audit_overlap import audit_corpus_overlap

cache = json.load(open("data/cache/postgresql-16-US.pdf.extracted.json", "r", encoding="utf-8"))
pages = [(p["page_number"], p["text"]) for p in cache["pages"]]

md_manifest = json.load(open("data/markdown-corpus-manifest.json", "r", encoding="utf-8"))
md_docs = []
for d in md_manifest["documents"]:
    fpath = os.path.join("data/raw/markdown", d["filename"])
    content = open(fpath, "r", encoding="utf-8").read()
    md_docs.append({
        "document_id": d["document_id"],
        "filename": d["filename"],
        "title": d["title"],
        "content": content,
    })

res = audit_corpus_overlap(pages, md_docs)

print(f"Exact SHA-256 collisions: {res['exact_hash_collisions_count']}")
print(f"Total Markdown docs analyzed: {res['markdown_documents_audited']}")
print("-" * 70)
for m in res["markdown_mappings"]:
    pages_str = ", ".join(f"p.{p['page_number']}({p['containment_pct']}%)" for p in m["matching_pdf_pages"][:3])
    print(f"{m['filename']:<25} -> {m['likely_page_range']:<18} Containment: {m['overall_shingle_containment_pct']}% | Top: {pages_str}")
