# Markdown Corpus Verification Report: PostgreSQL 16 Reference Set

> **ASSESSMENT REQUIREMENT REQ-02 PREPARATION**  
> **Status**: **VERIFIED & PASSED**  
> **Verification Date**: 2026-10-23  
> **Total Documents Verified**: **14 documents** (Requirement: $\ge 10$ documents)  
> **Total Word Count**: **44,738 words**  
> **Total Headings Preserved**: **168**  
> **Total Code Blocks Preserved**: **123**  
> **Scope Notice**: This report verifies the prepared Markdown source files and metadata. Ingestion parsing, chunking, and embedding generation have **not** yet been implemented.

---

## 1. Corpus Summary & Provenance

All documents were retrieved from the official PostgreSQL 16 HTML documentation hub at [https://www.postgresql.org/docs/16/](https://www.postgresql.org/docs/16/) and converted to structured Markdown. All navigation headers, footers, TOC sidebars, and search forms were stripped, while heading hierarchies (`#`, `##`, `###`), tables, and code listings (` ```sql `) were preserved.

- **License**: The PostgreSQL License (OSI-approved, BSD-style permissive).
- **Target Directory**: `data/raw/markdown/`
- **Manifest Location**: `data/markdown-corpus-manifest.json`

---

## 2. Inventory of Verified Documents

| # | Filename | Topic | Document Title | Words | Headings | Code | SHA-256 (first 16 chars) |
| :- | :--- | :--- | :--- | :- | :- | :- | :--- |
| 1 | `auth-pg-hba-conf.md` | Client Authentication & Security | 21.1. The pg_hba.conf File | 3,648 | 70 | 3 | `3cad33df684d0cfc...` |
| 2 | `backup-dump.md` | Backup & Restore | 26.1. SQL Dump | 1,543 | 5 | 15 | `0b46e465b0e3d79c...` |
| 3 | `ddl-partitioning.md` | Table Partitioning | 5.11. Table Partitioning | 6,378 | 14 | 29 | `0bbac6b66fa8d58e...` |
| 4 | `explicit-locking.md` | Concurrency & Locking | 13.3. Explicit Locking | 2,788 | 7 | 4 | `e67edd5fb723ce24...` |
| 5 | `indexes-partial.md` | Partial Indexes | 11.8. Partial Indexes | 1,501 | 1 | 11 | `41b65e76473325d6...` |
| 6 | `indexes-types.md` | Index Types (B-Tree, GIN, BRIN, GiST) | 11.2. Index Types | 985 | 7 | 8 | `e20c63a30dec492c...` |
| 7 | `mvcc-intro.md` | Transactions & Concurrency | 13.1. Introduction | 237 | 1 | 0 | `99fc6ce1ce08c258...` |
| 8 | `postgres-fdw.md` | Foreign Data Wrappers | F.38. postgres_fdw — access data stored in external PostgreSQL servers | 5,052 | 21 | 8 | `75bd6cd4b3bc3cfe...` |
| 9 | `routine-vacuuming.md` | Maintenance & Vacuuming | 25.1. Routine Vacuuming | 5,560 | 17 | 6 | `f1f6ad41216fa7b2...` |
| 10 | `runtime-config-resource.md` | Server Configuration | 20.4. Resource Consumption | 5,316 | 8 | 0 | `0242a0aa8ce2c895...` |
| 11 | `textsearch-intro.md` | Full-Text Search | 12.1. Introduction | 2,165 | 5 | 7 | `969d7340980decec...` |
| 12 | `transaction-iso.md` | Transactions & Concurrency | 13.2. Transaction Isolation | 3,707 | 6 | 7 | `9afce5881678c5bf...` |
| 13 | `using-explain.md` | Query Optimization | 14.1. Using EXPLAIN | 5,443 | 4 | 25 | `60b01fd151da48de...` |
| 14 | `wal-intro.md` | Write-Ahead Logging | 30.3. Write-Ahead Logging (WAL) | 415 | 2 | 0 | `a5ab240f4a707b62...` |

---

## 3. Provenance and Source URL Traceability

Each document contains standardized YAML frontmatter linking back to its official canonical source URL:

- **`auth-pg-hba-conf.md`**: [https://www.postgresql.org/docs/16/auth-pg-hba-conf.html](https://www.postgresql.org/docs/16/auth-pg-hba-conf.html)
- **`backup-dump.md`**: [https://www.postgresql.org/docs/16/backup-dump.html](https://www.postgresql.org/docs/16/backup-dump.html)
- **`ddl-partitioning.md`**: [https://www.postgresql.org/docs/16/ddl-partitioning.html](https://www.postgresql.org/docs/16/ddl-partitioning.html)
- **`explicit-locking.md`**: [https://www.postgresql.org/docs/16/explicit-locking.html](https://www.postgresql.org/docs/16/explicit-locking.html)
- **`indexes-partial.md`**: [https://www.postgresql.org/docs/16/indexes-partial.html](https://www.postgresql.org/docs/16/indexes-partial.html)
- **`indexes-types.md`**: [https://www.postgresql.org/docs/16/indexes-types.html](https://www.postgresql.org/docs/16/indexes-types.html)
- **`mvcc-intro.md`**: [https://www.postgresql.org/docs/16/mvcc-intro.html](https://www.postgresql.org/docs/16/mvcc-intro.html)
- **`postgres-fdw.md`**: [https://www.postgresql.org/docs/16/postgres-fdw.html](https://www.postgresql.org/docs/16/postgres-fdw.html)
- **`routine-vacuuming.md`**: [https://www.postgresql.org/docs/16/routine-vacuuming.html](https://www.postgresql.org/docs/16/routine-vacuuming.html)
- **`runtime-config-resource.md`**: [https://www.postgresql.org/docs/16/runtime-config-resource.html](https://www.postgresql.org/docs/16/runtime-config-resource.html)
- **`textsearch-intro.md`**: [https://www.postgresql.org/docs/16/textsearch-intro.html](https://www.postgresql.org/docs/16/textsearch-intro.html)
- **`transaction-iso.md`**: [https://www.postgresql.org/docs/16/transaction-iso.html](https://www.postgresql.org/docs/16/transaction-iso.html)
- **`using-explain.md`**: [https://www.postgresql.org/docs/16/using-explain.html](https://www.postgresql.org/docs/16/using-explain.html)
- **`wal-intro.md`**: [https://www.postgresql.org/docs/16/wal-intro.html](https://www.postgresql.org/docs/16/wal-intro.html)

---

## 4. Verification Command & Reproducibility

To independently reproduce this verification:

```cmd
python scripts/verify_markdown_corpus.py
```

### Verification Standard
- [x] $\ge 10$ Markdown source documents present (14 verified).
- [x] Every file is non-empty and contains valid frontmatter (`document_id`, `source_url`, `title`, `topic`).
- [x] All URLs point to official `https://www.postgresql.org/docs/16/`.
- [x] Cryptographic SHA-256 hashes generated dynamically without hard-coding.
- [x] Code blocks and heading hierarchies verified present.

---

## 5. Scope Boundaries

- **Verified**: Markdown source documents created, cleaned, and verified on disk.
- **Unstarted**: Document ingestion worker, chunking, embedding generation, vector indexing, chat UI, evaluation.
