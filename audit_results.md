# Rapport d'Audit Technique : A2A Knowledge Mesh

## 1. Résumé Exécutif
- Le projet **A2A Knowledge Mesh** est une implémentation robuste et propre d'un maillage de connaissances décentralisé basé sur des agents Starlette légers. L'infrastructure est saine, les tests (41 tests unitaires et 8 étapes de test d'intégration e2e) s'exécutent avec succès et valident les fonctionnalités clés d'authentification, de stockage et de réconciliation de faits.
- **Métriques clés :**
  - **Fichiers totaux indexés :** 50 fichiers (après retrait de `mesh_agent.py`).
  - **Fichiers dans le scope de l'audit :** 29 fichiers.
  - **Fichiers hors-scope ignorés (seuil < 40%) :** 21 fichiers (dossiers `spikes/` et `docs/`).

## 2. Cohérence Architecturale
L'architecture implémentée correspond fidèlement aux spécifications techniques de [AGENTS.md](file:///home/mael/mael/Dev/band/a2a-knowledge-mesh/AGENTS.md) :
- **Trois agents ASGI Starlette indépendants :**
  - `Registry` (port 8765) gère la découverte et l'enregistrement.
  - `Keeper` (port 8766) stocke les faits RDF-lite dans SQLite.
  - `Reconciler` (port 8767) détecte les contradictions et gère la résolution via LLM.
- **Double saveur d'agents :** Les agents A2A HTTP locaux (`agents/{registry,keeper,reconciler}.py`) coexistent proprement avec les agents WebSocket Band-native (`agents/*_band.py`) et partagent les classes de base (`agents/base.py` et `agents/band_agent.py`).
- **Transport et Sécurité :** L'authentification basée sur des jetons de rôle (`A2A_{ROLE}_TOKEN`) combinée à une signature optionnelle HMAC du corps de requête sur le endpoint `/a2a` fonctionne parfaitement comme validé par les tests.

## 3. Qualité du Code & Bugs Potentiels
- **Bug d'annotation résolu :** Le bug d'annotation dans `agents/reconciler.py:L935` (où `HTMLResponse` était indéfini au niveau global) a été corrigé en déplaçant son import sous le bloc `if TYPE_CHECKING:`.
- **Intégration et chargement du LLM :** La résolution LLM pour les suggestions de conflits est désormais entièrement opérationnelle. L'environnement `.env` est correctement chargé au démarrage autonome des agents (`reconciler`, `keeper`, `registry`) ainsi que dans `test_integration.py`. Les tests d'intégration exploitent désormais directement Featherless (Qwen 2.5 14B) avec succès pour générer des justifications intelligentes.
- **Avertissements de dépréciation :** `test_unit.py` lève toujours un avertissement de dépréciation de Starlette (`StarletteDeprecationWarning`) concernant l'utilisation de `httpx` avec `starlette.testclient`.

## 4. Code Inutilisé & Nettoyage
- **`agents/mesh_agent.py` :** Supprimé du dépôt.
- **`agents/ingester.py` et `agents/llm_extractor.py` :** Ces deux composants d'ingestion de faits et d'extraction via LLM ne sont pas intégrés dans la boucle d'exécution des agents ou du runner et restent purement autonomes.
- **`scripts/` :** Les scripts de scraping (`git_scraper.py` et `scraper.py`) sont documentés comme "standalone" et ne participent pas à l'exécution de la mesh d'agents.
- **`spikes/` :** Contient 6 sous-dossiers d'expérimentation technique obsolètes ou archivés.

## 5. Recommandations Restantes

| Priorité | Cible | Description | Action conseillée |
| :--- | :--- | :--- | :--- |
| **MOYENNE** | [pyproject.toml](file:///home/mael/mael/Dev/band/a2a-knowledge-mesh/pyproject.toml#L18-L20) | Incohérence de nommage de script | Le script `mesh` dans `pyproject.toml` pointe vers `agents.runner:main`, tandis que le client CLI Band-native s'appelle `mesh.py`. Il est recommandé de renommer l'entrée de script en `runner` ou `mesh-runner` pour éviter toute confusion. |
| **BASSE** | `agents/ingester.py` / `llm_extractor.py` | Pipeline d'ingestion orphelin | Intégrer formellement ces pipelines dans le runner ou documenter clairement leur mode d'utilisation autonome. |
| **BASSE** | `spikes/` | Fichiers d'expérimentation temporaires | Nettoyer ou archiver les dossiers de spikes pour alléger le dépôt. |
