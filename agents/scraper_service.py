"""Scraper Service — LLM-powered repo analysis.

Scans a repository directory, uses LLM to extract structured facts
from both code files and documentation files, then identifies
semantic conflicts between what the code says and what the docs say.

The extracted facts are returned as structured dicts ready for
batch submission to Keeper via Band.

Usage:
    from agents.scraper_service import scan_repo

    facts, conflicts = await scan_repo("/path/to/repo")
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from agents.provider import Provider, resolve_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java", ".kt",
    ".swift", ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".scala",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".ini", ".cfg",
    ".dockerfile", ".sql", ".sh", ".bash", ".zsh",
}

DOC_EXTENSIONS = {
    ".md", ".mdx", ".rst", ".txt", ".adoc",
    ".html", ".pdf",
}

CONFIG_FILES = {
    "pyproject.toml", "package.json", "Cargo.toml", "Makefile",
    "docker-compose.yml", "docker-compose.yaml", ".env.example",
    "go.mod", "go.sum", "Gemfile", "Pipfile", "requirements.txt",
    "composer.json", "build.gradle", "BUILD",
}

IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".tox", ".eggs", "egg-info",
    ".idea", ".vscode", ".DS_Store", "target", "vendor",
}

MAX_FILE_SIZE = 50 * 1024  # 50 KB max per file for LLM analysis
MAX_FILES = 30  # max files to analyze per run


def _classify_file(filepath: Path) -> str | None:
    """Return 'code', 'doc', or None (skip binary/unknown)."""
    ext = filepath.suffix.lower()
    name = filepath.name.lower()

    if ext in CODE_EXTENSIONS or name in CONFIG_FILES:
        return "code"
    if ext in DOC_EXTENSIONS:
        return "doc"
    return None


def _find_relevant_files(repo_path: Path) -> list[Path]:
    """Find code and doc files in the repo, sorted by importance."""
    files: list[tuple[int, Path, str]] = []  # (priority, path, type)

    for f in repo_path.rglob("*"):
        if not f.is_file():
            continue

        rel = f.relative_to(repo_path)
        # Skip ignored directories
        parts = rel.parts
        if any(p in IGNORE_DIRS for p in parts):
            continue

        # Skip files in hidden directories (except root-level .env)
        if any(p.startswith(".") and p not in (".env", ".env.example") for p in parts):
            continue

        # Skip binary/large files
        try:
            size = f.stat().st_size
            if size == 0 or size > MAX_FILE_SIZE:
                continue
            # Quick binary check
            if b"\x00" in f.read_bytes()[:1024]:
                continue
        except (OSError, PermissionError):
            continue

        file_type = _classify_file(f)
        if file_type is None:
            continue

        # Priority: config files first, then code, then docs
        name = f.name.lower()
        if name in CONFIG_FILES:
            priority = 0
        elif file_type == "code":
            priority = 1
        else:
            priority = 2

        files.append((priority, f, file_type))

    # Sort by priority, then path depth
    files.sort(key=lambda x: (x[0], len(str(x[1])), str(x[1])))
    return [f[1] for f in files[:MAX_FILES]]


# ---------------------------------------------------------------------------
# LLM Fact Extraction
# ---------------------------------------------------------------------------

FACT_EXTRACTION_PROMPT = """You are analyzing a file from a code repository.
Extract ALL structured facts from this file content as a JSON array.

Each fact must be an object with:
- "subject": the main entity (project name, module, component — be specific)
- "predicate": what this fact says about the subject (use short kebab-case names)
- "object": the value or statement
- "source_type": "{source_type}" (code or doc)
- "category": one of: "version", "dependency", "architecture", "feature", "api", "config", "description", "usage"

Rules:
1. Extract facts that describe what the code/project IS, HAS, or DOES
2. For code files: extract build config, dependencies, APIs, architecture, env vars
3. For doc files: extract version claims, feature descriptions, architecture docs, usage examples, setup instructions
4. Be precise — use exact values from the file
5. Extract BOTH explicit facts (version=1.0.0) AND semantic facts (architecture=event-driven, has-feature=conflict-detection)
6. Include the project name as subject whenever you can determine it

Return ONLY the JSON array — no explanation, no markdown fences.

File path: {filepath}
File type: {source_type}

Content:
```
{content}
```"""

CONFLICT_DETECTION_PROMPT = """You are a knowledge reconciliation expert. Compare these two sets of
facts extracted from the SAME repository — one from CODE files, one from DOCUMENTATION files.

Identify contradictions where:
1. A code file says version X, a doc file says version Y
2. Code implements feature/API, doc describes it differently
3. Code requires dependency X, doc says dependency Y
4. Code has architecture/behavior, doc describes it differently
5. Any factual claim in docs that contradicts code

Return a JSON array of conflicts:
[
  {{
    "subject": "entity name",
    "predicate": "what differs",
    "code_value": "what code says",
    "doc_value": "what doc says",
    "code_file": "path",
    "doc_file": "path",
    "severity": "LOW|MEDIUM|HIGH|CRITICAL",
    "reason": "why this is a contradiction"
  }}
]

If no contradictions exist, return an empty array.

CODE FACTS:
{code_facts}

DOC FACTS:
{doc_facts}
"""


async def _extract_facts_from_file(
    filepath: Path,
    repo_path: Path,
    source_type: str,
    provider: Provider,
    config: Any,
) -> list[dict]:
    """Use LLM to extract structured facts from a single file."""
    try:
        content = filepath.read_text(errors="replace")
    except Exception as e:
        logger.warning("Cannot read %s: %s", filepath, e)
        return []

    # Truncate content for LLM (keep head + tail)
    if len(content) > 6000:
        content = content[:3000] + "\n\n... [truncated] ...\n\n" + content[-2500:]

    rel_path = str(filepath.relative_to(repo_path))
    prompt = FACT_EXTRACTION_PROMPT.format(
        filepath=rel_path,
        source_type=source_type,
        content=content,
    )

    result = await provider.chat_completion(
        system="You extract structured facts from code repository files. Return only JSON.",
        user=prompt,
        config=config,
        temperature=0.1,
        max_tokens=2000,
        parse_json=True,
    )

    if isinstance(result, list):
        # Add source metadata
        for fact in result:
            if isinstance(fact, dict):
                fact.setdefault("source_id", rel_path)
                fact.setdefault("source_type", source_type)
                fact.setdefault("source_url", filepath.as_uri())
        return result
    elif isinstance(result, dict):
        # Might be wrapped in a "facts" key
        facts = result.get("facts", result.get("data", []))
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, dict):
                    fact.setdefault("source_id", rel_path)
                    fact.setdefault("source_type", source_type)
                    fact.setdefault("source_url", filepath.as_uri())
            return facts

    logger.warning("Unexpected LLM response shape for %s: %s", rel_path, type(result))
    return []


async def _detect_code_doc_conflicts(
    code_facts: list[dict],
    doc_facts: list[dict],
    provider: Provider,
    config: Any,
) -> list[dict]:
    """Use LLM to find contradictions between code and documentation."""
    if not code_facts or not doc_facts:
        return []

    code_json = json.dumps(code_facts, indent=2, ensure_ascii=False)
    doc_json = json.dumps(doc_facts, indent=2, ensure_ascii=False)

    # Truncate if too long
    max_len = 4000
    if len(code_json) > max_len:
        code_json = code_json[:max_len] + "\n... [truncated]"
    if len(doc_json) > max_len:
        doc_json = doc_json[:max_len] + "\n... [truncated]"

    prompt = CONFLICT_DETECTION_PROMPT.format(
        code_facts=code_json,
        doc_facts=doc_json,
    )

    result = await provider.chat_completion(
        system="You detect contradictions between code and documentation. Return only JSON.",
        user=prompt,
        config=config,
        temperature=0.1,
        max_tokens=2000,
        parse_json=True,
    )

    if isinstance(result, list):
        return result
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scan_repo(
    repo_path: str | Path,
    provider_instance: Provider | None = None,
    llm_config: Any = None,
) -> dict:
    """Scan a repository with LLM and return facts + conflicts.

    Returns:
        {
            "facts": [all extracted facts],
            "conflicts": [code-vs-doc contradictions],
            "code_files": N,
            "doc_files": N,
            "total_facts": N,
            "repo_name": "..."
        }
    """
    path = Path(repo_path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Not a directory: {path}")

    provider = provider_instance or Provider(timeout=60.0, max_retries=2)
    config = llm_config or resolve_config()
    if config is None:
        logger.warning("No LLM provider configured — using mock analysis")
        return {"facts": [], "conflicts": [], "error": "No LLM provider"}

    repo_name = path.name
    files = _find_relevant_files(path)
    logger.info("Repo %s: found %d relevant files", repo_name, len(files))

    # ── Extract facts in parallel (concurrent LLM calls) ────────────
    all_facts: list[dict] = []
    code_facts: list[dict] = []
    doc_facts: list[dict] = []
    # Lock for thread-safe list appends
    _lock = asyncio.Lock()

    async def _analyze_one(filepath: Path) -> None:
        source_type = _classify_file(filepath) or "code"
        rel = filepath.relative_to(path)
        logger.info("  Analyzing %s (%s)", rel, source_type)
        try:
            facts = await _extract_facts_from_file(
                filepath, path, source_type, provider, config
            )
            async with _lock:
                all_facts.extend(facts)
                if source_type == "code":
                    code_facts.extend(facts)
                else:
                    doc_facts.extend(facts)
            logger.info("    → %d facts from %s", len(facts), rel)
        except Exception as e:
            logger.warning("    → Error %s: %s", rel, e)

    # Run in parallel batches of 3 (avoids rate limits)
    batch_size = 3
    for i in range(0, len(files), batch_size):
        batch = files[i:i + batch_size]
        await asyncio.gather(*[_analyze_one(f) for f in batch])

    # Detect code vs doc conflicts
    conflicts: list[dict] = []
    if code_facts and doc_facts:
        logger.info("Detecting code vs doc conflicts (%d code, %d doc facts)...",
                     len(code_facts), len(doc_facts))
        try:
            conflicts = await _detect_code_doc_conflicts(
                code_facts, doc_facts, provider, config
            )
            logger.info("  → %d conflicts detected", len(conflicts))
        except Exception as e:
            logger.warning("  → Conflict detection error: %s", e)

    result = {
        "facts": all_facts,
        "conflicts": conflicts,
        "code_files": sum(1 for f in files if _classify_file(f) == "code"),
        "doc_files": sum(1 for f in files if _classify_file(f) == "doc"),
        "total_facts": len(all_facts),
        "repo_name": repo_name,
        "files_analyzed": len(files),
    }

    logger.info(
        "Scan complete: %d facts from %d files, %d code-vs-doc conflicts",
        result["total_facts"], result["files_analyzed"], len(conflicts),
    )
    return result


async def scan_self(provider_instance: Provider | None = None, llm_config: Any = None) -> dict:
    """Convenience: scan the knowledge-mesh repo itself."""
    repo_path = Path(__file__).parent.parent  # project root
    return await scan_repo(repo_path, provider_instance, llm_config)
