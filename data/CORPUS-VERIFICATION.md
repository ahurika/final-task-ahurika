# Corpus Verification Report: PostgreSQL 16 Documentation

> **ASSESSMENT REQUIREMENT REQ-01 VERIFICATION**  
> **Status**: **VERIFIED & PASSED**  
> **Verification Date**: 2026-10-23  
> **Measured Page Count**: **3,220 pages** (Requirement: $\ge 100$ pages)  
> **Multiplier**: **32.2× the required threshold**  
> **Note on Scope**: This document verifies the raw corpus size and physical integrity. Document parsing, dual chunking, embedding generation, and vector indexing are subsequent implementation stages and have **not** yet been built.

---

## 1. Corpus Provenance & Licensing

- **Corpus Title**: Official PostgreSQL 16 Documentation (US Letter Edition)
- **Official Documentation Hub**: [https://www.postgresql.org/docs/16/](https://www.postgresql.org/docs/16/)
- **Direct Download Source URL**: [https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf](https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf)
- **License**: **The PostgreSQL License** (OSI-approved, BSD-style permissive license).
  - License Terms: [https://www.postgresql.org/about/licence/](https://www.postgresql.org/about/licence/)
  - Legal Permissibility: Free for academic, educational, commercial, and research use without licensing fees or distribution restrictions.
- **Collection Timestamp**: 2026-10-23T15:28:19Z

---

## 2. Cryptographic & Physical Artifact Evidence

The artifact was downloaded over HTTPS directly into `data/raw/postgresql-16-US.pdf` and verified using `scripts/verify_corpus.py`:

| Property | Measured Value | Verification Method |
| :--- | :--- | :--- |
| **Local File Path** | `data/raw/postgresql-16-US.pdf` | Relative to project root |
| **File Format** | Portable Document Format (PDF 1.5) | Magic bytes verified (`%PDF-`) |
| **File Size (Bytes)** | **15,162,468 bytes** | `os.path.getsize()` |
| **File Size (MB)** | **14.46 MB** | Metric conversion |
| **SHA-256 Checksum** | `f5316371014f5119fe51c153fffe11185b03504dce46361102761107e77c83ba` | 1 MB chunked stream hash |
| **Actual Page Count** | **3,220 pages** | `pypdf.PdfReader` catalog length |
| **Assessment Minimum** | **100 pages** | PRD REQ-01 / AC-01 standard |
| **Compliance Status** | **PASSED** (3,220 $\ge$ 100) | Deterministic boolean check |

---

## 3. Reproduction Command & Console Output

To independently reproduce this verification at any time, execute the following command from the workspace root:

```cmd
python scripts/verify_corpus.py
```

### Exact Terminal Execution Transcript (Executed 2026-10-23)
```text
======================================================================
TASK 4 — REPRODUCIBLE CORPUS SIZE VERIFICATION (REQ-01)
======================================================================
Project Root         : C:\Users\USER\Desktop\RAG Task
Target PDF Path      : C:\Users\USER\Desktop\RAG Task\data\raw\postgresql-16-US.pdf
Minimum Page Target  : 100 pages
----------------------------------------------------------------------
Computing SHA-256 checksum...
Opening PDF and parsing page catalog via pypdf...
----------------------------------------------------------------------
File Name            : postgresql-16-US.pdf
File Size            : 15,162,468 bytes (~14.46 MB)
SHA-256 Hash         : f5316371014f5119fe51c153fffe11185b03504dce46361102761107e77c83ba
Measured Page Count  : 3,220 pages
Assessment Minimum   : 100 pages
Verification Result  : PASSED (3,220 >= 100)
----------------------------------------------------------------------
Manifest written to  : C:\Users\USER\Desktop\RAG Task\data\corpus-manifest.json
======================================================================
SUCCESS: REQ-01 Corpus size requirement mathematically verified.
======================================================================
```

A subsequent re-run confirmed 100% deterministic consistency across all values.

---

## 4. Dependencies Added

To perform deterministic PDF structural validation without binary C-extensions, the following lightweight development dependency was installed and recorded in [requirements-dev.txt](file:///c:/Users/USER/Desktop/RAG%20Task/requirements-dev.txt):
- `pypdf` (version 6.21.2): Pure-Python PDF extraction library.
- Justification: Enables in-memory structural validation and page catalog traversal without installing heavyweight application dependencies (such as FastAPI, Uvicorn, or pgvector drivers) prematurely.

---

## 5. Scope Boundaries & Next Steps

- **Completed**: Raw document collection, integrity validation, SHA-256 hashing, and page count verification.
- **Pending (Next Phase)**:
  1. Preparing the Markdown source documents (converting core PostgreSQL HTML/SGML reference chapters to `.md`).
  2. Planning and designing the background ingestion pipeline (REQ-02 and REQ-03).
