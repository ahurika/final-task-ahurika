"""
Database Migration Manager & Offline Verification Engine
Location: src/db/migrator.py

Handles discovery, dependency ordering, checksum hashing, parameterization,
and dry-run plan generation for PostgreSQL + pgvector migrations.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Any


# PostgreSQL pgvector limits: HNSW indexes support up to 2,000 dimensions
MIN_VECTOR_DIMENSION = 1
MAX_HNSW_VECTOR_DIMENSION = 2000


def validate_embedding_dimension(dim: Any) -> int:
    """
    Validates that the provided embedding dimension is a safe, positive integer
    within pgvector's HNSW index limits (1 to 2,000 dimensions).

    Rejects non-integers (including booleans, strings, floats, None),
    zero, negative values, and dimensions exceeding pgvector's HNSW limit.
    """
    if isinstance(dim, bool) or not isinstance(dim, int):
        raise TypeError(
            f"Embedding dimension must be an integer, got {type(dim).__name__}: {dim!r}"
        )
    if dim < MIN_VECTOR_DIMENSION:
        raise ValueError(
            f"Embedding dimension must be at least {MIN_VECTOR_DIMENSION}, got {dim}"
        )
    if dim > MAX_HNSW_VECTOR_DIMENSION:
        raise ValueError(
            f"Embedding dimension {dim} exceeds maximum of {MAX_HNSW_VECTOR_DIMENSION} "
            f"supported by PostgreSQL pgvector for HNSW indexes (hnsw limit is 2,000 dimensions)."
        )
    return dim


@dataclass(frozen=True)
class MigrationStep:
    version: int
    name: str
    up_filename: str
    rollback_filename: str
    up_path: str
    rollback_path: str
    up_sha256: str
    rollback_sha256: str
    up_sql_template: str
    rollback_sql: str

    def render_up_sql(self, embedding_dimension: int = 768) -> str:
        """Renders UP SQL with parameterized and validated embedding dimension."""
        validated_dim = validate_embedding_dimension(embedding_dimension)
        # Replace /* __EMBEDDING_DIM__ */ <digits> with the target dimension
        pattern = r"/\*\s*__EMBEDDING_DIM__\s*\*/\s*\d+"
        replacement = f"/* __EMBEDDING_DIM__ */ {validated_dim}"
        return re.sub(pattern, replacement, self.up_sql_template)


class MigrationManager:
    """Manages versioned SQL migrations and rollback safety."""

    def __init__(self, migrations_dir: Optional[str] = None):
        if migrations_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            migrations_dir = os.path.join(project_root, "migrations")
        self.migrations_dir = migrations_dir

    def discover_migrations(self) -> List[MigrationStep]:
        """
        Discovers all versioned migrations in the migrations directory.
        Enforces strict naming convention: ^(\\d{3})_([a-z0-9_]+)\\.sql$
        Ensures matching .rollback.sql exists for each.
        """
        if not os.path.isdir(self.migrations_dir):
            raise FileNotFoundError(f"Migrations directory not found: {self.migrations_dir}")

        files = os.listdir(self.migrations_dir)
        up_pattern = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
        rollback_pattern = re.compile(r"^(\d{3})_([a-z0-9_]+)\.rollback\.sql$")

        discovered: Dict[int, Dict[str, str]] = {}

        for filename in files:
            up_match = up_pattern.match(filename)
            rollback_match = rollback_pattern.match(filename)

            if rollback_match:
                ver = int(rollback_match.group(1))
                discovered.setdefault(ver, {})["rollback_filename"] = filename
            elif up_match:
                ver = int(up_match.group(1))
                discovered.setdefault(ver, {})["up_filename"] = filename
                discovered[ver]["name"] = up_match.group(2)

        steps: List[MigrationStep] = []
        sorted_versions = sorted(discovered.keys())

        # Verify sequential ordering starting at 1 with no gaps
        for expected_idx, ver in enumerate(sorted_versions, start=1):
            if ver != expected_idx:
                raise ValueError(
                    f"Migration sequence gap detected: expected version {expected_idx:03d}, found {ver:03d}"
                )

            entry = discovered[ver]
            if "up_filename" not in entry:
                raise ValueError(f"Missing UP migration file for version {ver:03d}")
            if "rollback_filename" not in entry:
                raise ValueError(
                    f"Missing ROLLBACK migration file for version {ver:03d} (expected {ver:03d}_{entry.get('name')}.rollback.sql)"
                )

            up_path = os.path.join(self.migrations_dir, entry["up_filename"])
            rollback_path = os.path.join(self.migrations_dir, entry["rollback_filename"])

            with open(up_path, "r", encoding="utf-8") as f:
                up_sql = f.read()

            with open(rollback_path, "r", encoding="utf-8") as f:
                rollback_sql = f.read()

            up_hash = hashlib.sha256(up_sql.encode("utf-8")).hexdigest()
            rollback_hash = hashlib.sha256(rollback_sql.encode("utf-8")).hexdigest()

            steps.append(
                MigrationStep(
                    version=ver,
                    name=entry["name"],
                    up_filename=entry["up_filename"],
                    rollback_filename=entry["rollback_filename"],
                    up_path=up_path,
                    rollback_path=rollback_path,
                    up_sha256=up_hash,
                    rollback_sha256=rollback_hash,
                    up_sql_template=up_sql,
                    rollback_sql=rollback_sql,
                )
            )

        return steps

    def get_up_plan(self, embedding_dimension: int = 768) -> List[Dict[str, Any]]:
        """Returns ordered migration execution plan with validated, rendered SQL."""
        validated_dim = validate_embedding_dimension(embedding_dimension)
        steps = self.discover_migrations()
        plan = []
        for step in steps:
            plan.append({
                "version": step.version,
                "name": step.name,
                "action": "UP",
                "filename": step.up_filename,
                "sha256": step.up_sha256,
                "sql": step.render_up_sql(validated_dim),
            })
        return plan

    def get_down_plan(self) -> List[Dict[str, Any]]:
        """Returns reverse-ordered rollback execution plan."""
        steps = self.discover_migrations()
        plan = []
        for step in reversed(steps):
            plan.append({
                "version": step.version,
                "name": step.name,
                "action": "ROLLBACK",
                "filename": step.rollback_filename,
                "sha256": step.rollback_sha256,
                "sql": step.rollback_sql,
            })
        return plan

    def render_full_ddl(self, embedding_dimension: int = 768) -> str:
        """Combines all UP migrations into a single reproducible SQL script with validated dimension."""
        validated_dim = validate_embedding_dimension(embedding_dimension)
        plan = self.get_up_plan(embedding_dimension=validated_dim)
        output = [
            "-- =============================================================================",
            f"-- PostgreSQL Documentation Assistant: Full DDL Schema (Generated)",
            f"-- Target Embedding Dimension: {validated_dim} (Validated: 1 <= dim <= 2000)",
            "-- =============================================================================\n",
        ]
        for item in plan:
            output.append(f"-- >>> BEGIN MIGRATION {item['version']:03d}: {item['name']} ({item['filename']}) >>>")
            output.append(item["sql"].strip())
            output.append(f"-- <<< END MIGRATION {item['version']:03d}: {item['name']} <<<\n")
        return "\n\n".join(output)

    def validate_offline(self) -> Dict[str, Any]:
        """Performs static offline validation of migration integrity."""
        steps = self.discover_migrations()
        errors = []
        warnings = []

        total_up_statements = 0
        total_rollback_statements = 0

        for step in steps:
            # Check non-empty SQL
            if not step.up_sql_template.strip():
                errors.append(f"Migration {step.up_filename} is empty.")
            if not step.rollback_sql.strip():
                errors.append(f"Rollback {step.rollback_filename} is empty.")

            # Check for secrets or hardcoded passwords
            forbidden_patterns = [
                r"password\s*=\s*['\"][^'\"]+['\"]",
                r"AIza[0-9A-Za-z-_]{35}",
                r"sk-[a-zA-Z0-9]{32,}",
                r"postgres://[a-zA-Z0-9_-]+:[^@]+@",
            ]
            for pat in forbidden_patterns:
                if re.search(pat, step.up_sql_template, re.IGNORECASE):
                    errors.append(f"Forbidden credential pattern matched in {step.up_filename}")
                if re.search(pat, step.rollback_sql, re.IGNORECASE):
                    errors.append(f"Forbidden credential pattern matched in {step.rollback_filename}")

            # Check for undefined custom type patterns (e.g. uuid_array, float_array)
            undefined_type_patterns = [
                r"\buuid_array\b",
                r"\bfloat_array\b",
                r"\bvector_array\b",
            ]
            for pat in undefined_type_patterns:
                if re.search(pat, step.up_sql_template, re.IGNORECASE):
                    errors.append(f"Undefined custom type pattern '{pat}' found in {step.up_filename}. Must use native PostgreSQL array types (e.g. UUID[], FLOAT8[]).")
                if re.search(pat, step.rollback_sql, re.IGNORECASE):
                    errors.append(f"Undefined custom type pattern '{pat}' found in {step.rollback_filename}.")

            # Basic statement count heuristic
            up_stmts = [s for s in step.up_sql_template.split(";") if s.strip()]
            down_stmts = [s for s in step.rollback_sql.split(";") if s.strip()]
            total_up_statements += len(up_stmts)
            total_rollback_statements += len(down_stmts)

        return {
            "valid": len(errors) == 0,
            "total_migrations": len(steps),
            "total_up_statements": total_up_statements,
            "total_rollback_statements": total_rollback_statements,
            "migrations": [f"{s.version:03d}_{s.name}" for s in steps],
            "errors": errors,
            "warnings": warnings,
        }


def cli_dimension_type(val_str: str) -> int:
    """CLI type converter enforcing safe integer vector dimensions between 1 and 2,000."""
    try:
        val = int(val_str)
    except ValueError:
        import argparse
        raise argparse.ArgumentTypeError(f"Invalid integer dimension: {val_str!r}")
    try:
        return validate_embedding_dimension(val)
    except (TypeError, ValueError) as err:
        import argparse
        raise argparse.ArgumentTypeError(str(err))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PostgreSQL + pgvector Migration Tool")
    parser.add_argument("--plan", action="store_true", help="Display migration execution plan")
    parser.add_argument("--verify", action="store_true", help="Run offline static validation")
    parser.add_argument("--render-ddl", action="store_true", help="Print combined DDL to stdout")
    parser.add_argument(
        "--dim",
        type=cli_dimension_type,
        default=768,
        help="Target embedding dimensions (default: 768; must satisfy 1 <= dim <= 2000)",
    )
    args = parser.parse_args()

    mgr = MigrationManager()

    if args.verify:
        res = mgr.validate_offline()
        print(f"Validation: {'PASSED' if res['valid'] else 'FAILED'}")
        print(f"Total Migrations: {res['total_migrations']}")
        for m in res["migrations"]:
            print(f"  - {m}")
        if res["errors"]:
            print("Errors:")
            for e in res["errors"]:
                print(f"  [ERROR] {e}")
    elif args.render_ddl:
        print(mgr.render_full_ddl(embedding_dimension=args.dim))
    else:
        plan = mgr.get_up_plan(embedding_dimension=args.dim)
        print(f"Migration Plan (Target Dimension: {args.dim}):")
        for p in plan:
            print(f"  Step {p['version']:03d} [{p['action']}]: {p['name']} ({p['filename']}) - SHA256: {p['sha256'][:12]}...")
