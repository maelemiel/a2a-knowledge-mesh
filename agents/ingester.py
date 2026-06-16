"""Ingestion pipeline — scraper framework for feeding facts into the Keeper.

Each scraper implements ``Scraper`` and produces ``Fact`` dicts which
are batched and sent to Keeper via the authenticated ``a2a_call`` helper.

Built-in scrapers:
- ``PyprojectTomlScraper`` — extracts facts from a Python ``pyproject.toml``
- ``EnvFileScraper`` — extracts facts from a ``.env.example`` file

Extend by subclassing ``Scraper`` and implementing ``collect()``.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from agents.auth import a2a_call

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Fact:
    """A single fact to be stored in the Keeper."""

    subject: str
    predicate: str
    object: str
    source_id: str = "ingester"
    source_url: str | None = None


# ---------------------------------------------------------------------------
# Abstract scraper
# ---------------------------------------------------------------------------


class Scraper(ABC):
    """Base class for fact scrapers.

    Subclasses implement ``collect()`` which yields ``Fact`` instances.
    The ``Ingester`` manages batching and delivery.
    """

    name: str = "scraper"

    @abstractmethod
    async def collect(self) -> AsyncIterator[Fact]:
        """Yield facts discovered from the source."""
        if False:  # pragma: no cover — async generator type hint trick
            yield Fact("", "", "")

    def __repr__(self) -> str:
        return f"<Scraper {self.name}>"


# ---------------------------------------------------------------------------
# Built-in scrapers
# ---------------------------------------------------------------------------


class PyprojectTomlScraper(Scraper):
    """Scrape facts from a Python ``pyproject.toml`` file.

    Produces facts like::

        {subject: "a2a-knowledge-mesh", predicate: "python-version", object: ">=3.11"}
        {subject: "a2a-knowledge-mesh", predicate: "dependency", object: "uvicorn>=0.34"}
        {subject: "a2a-knowledge-mesh", predicate: "dependency", object: "starlette>=0.46"}
    """

    name = "pyproject-toml"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def collect(self) -> AsyncIterator[Fact]:  # type: ignore[misc]
        if not self.path.exists():
            logger.warning("pyproject.toml not found: %s", self.path)
            return

        import tomllib
        raw = self.path.read_text()
        data = tomllib.loads(raw)

        project = data.get("project", {})

        # Project name → subject
        subject = project.get("name", self.path.parent.name)

        # Python version
        python_ver = project.get("requires-python", "")
        if python_ver:
            yield Fact(subject=subject, predicate="python-version",
                       object=python_ver, source_id=self.name,
                       source_url=self.path.as_uri())

        # Dependencies
        for dep in project.get("dependencies", []):
            yield Fact(subject=subject, predicate="dependency",
                       object=dep, source_id=self.name,
                       source_url=self.path.as_uri())

        # Optional dependencies
        for opt_name, opt_deps in project.get("optional-dependencies", {}).items():
            for dep in opt_deps:
                yield Fact(subject=subject, predicate=f"optional-dependency:{opt_name}",
                           object=dep, source_id=self.name,
                           source_url=self.path.as_uri())

        logger.info("PyprojectTomlScraper collected facts for %s", subject)


class EnvFileScraper(Scraper):
    """Scrape facts from a ``.env.example`` file.

    Produces facts like::

        {subject: "a2a-knowledge-mesh", predicate: "env-var", object: "BAND_AGENT_ID=..."}
    """

    name = "env-file"

    def __init__(self, path: str | Path, *, subject: str = "") -> None:
        self.path = Path(path)
        self._subject = subject

    async def collect(self) -> AsyncIterator[Fact]:  # type: ignore[misc]
        if not self.path.exists():
            logger.warning("Env file not found: %s", self.path)
            return

        subject = self._subject or self.path.parent.name

        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                yield Fact(subject=subject, predicate="env-var",
                           object=f"{key.strip()}={value.strip()}",
                           source_id=self.name,
                           source_url=self.path.as_uri())

        logger.info("EnvFileScraper collected facts for %s", subject)


# ---------------------------------------------------------------------------
# Ingester — batches and sends facts to Keeper
# ---------------------------------------------------------------------------


class Ingester:
    """Collects facts from one or more scrapers and batch-inserts them
    into a Keeper agent via authenticated A2A RPC.

    Usage::

        ingester = Ingester(keeper_url="http://localhost:8766", target_role="keeper")
        ingester.add_scraper(PyprojectTomlScraper("pyproject.toml"))
        ingester.add_scraper(EnvFileScraper(".env.example"))
        await ingester.run()
    """

    def __init__(
        self,
        keeper_url: str = "http://localhost:8766",
        *,
        target_role: str = "keeper",
        batch_size: int = 100,
    ) -> None:
        self.keeper_url = keeper_url
        self.target_role = target_role
        self.batch_size = batch_size
        self.scrapers: list[Scraper] = []

    def add_scraper(self, scraper: Scraper) -> None:
        self.scrapers.append(scraper)
        logger.info("Ingester registered scraper: %s", scraper)

    async def run(self) -> dict:
        """Run all scrapers and send facts to Keeper.

        Returns summary: {scraper_name: facts_sent, ...}
        """
        if not self.scrapers:
            logger.warning("Ingester has no scrapers configured — nothing to do.")
            return {}

        summary: dict[str, int] = {}

        for scraper in self.scrapers:
            batch: list[dict[str, Any]] = []
            sent = 0

            async for fact in scraper.collect():
                batch.append({
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object": fact.object,
                    "source_id": fact.source_id,
                    "source_url": fact.source_url,
                })
                if len(batch) >= self.batch_size:
                    await self._send_batch(batch)
                    sent += len(batch)
                    batch = []

            # Flush remaining
            if batch:
                await self._send_batch(batch)
                sent += len(batch)

            summary[scraper.name] = sent
            logger.info("Scraper %s: sent %d facts", scraper.name, sent)

        return summary

    async def _send_batch(self, facts: list[dict]) -> None:
        """POST a batch of facts to Keeper."""
        try:
            result = await a2a_call(
                self.keeper_url,
                "store-facts-batch",
                {"facts": facts},
                target_role=self.target_role,
                timeout=30.0,
            )
            stored = len(result.get("facts", []))
            logger.debug("Batch stored: %d facts", stored)
        except Exception as e:
            logger.error("Failed to send batch of %d facts: %s", len(facts), e)
            raise


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


async def ingest_all(
    keeper_url: str = "http://localhost:8766",
    *,
    project_dir: str | Path | None = None,
    target_role: str = "keeper",
) -> dict:
    """Run all built-in scrapers against a project directory.

    Example::

        result = await ingest_all("http://localhost:8766", project_dir=".")
    """
    basedir = Path(project_dir) if project_dir else Path.cwd()
    ingester = Ingester(keeper_url, target_role=target_role)

    # Auto-detect available files
    pyproject = basedir / "pyproject.toml"
    if pyproject.exists():
        ingester.add_scraper(PyprojectTomlScraper(pyproject))

    env_file = basedir / ".env.example"
    if env_file.exists():
        ingester.add_scraper(EnvFileScraper(env_file))

    return await ingester.run()


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO)

    async def demo() -> None:
        pk = os.getenv("A2A_KEEPER_TOKEN")
        if not pk:
            logger.warning("A2A_KEEPER_TOKEN not set — ingestion may fail")

        result = await ingest_all("http://localhost:8766", project_dir=Path(__file__).parent.parent)
        print(json.dumps(result, indent=2))

    asyncio.run(demo())
