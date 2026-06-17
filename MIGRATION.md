# Migration: HTTP A2A → Band-Native Knowledge Mesh

## Why

Le hackathon demande:

> **"Band should be part of the actual collaboration layer, not only a thin wrapper."**

Actuellement tes agents communiquent en HTTP JSON-RPC. Band est un transport
secondaire. Le jury verra ça et dira "Band sert à rien — tu peux remplacer
par n'importe quel bus."

La bonne nouvelle: **tes 4 agents Band sont déjà implémentés et fonctionnent.**
Le problème c'est que le code HTTP (base.py, auth.py, runner.py) est encore
là, crée de la confusion, et dilue la démo.

### Ce que les meilleurs projets font que toi t'as pas

| Leçon | Qui | À copier |
|-------|-----|----------|
| DEMO.md mot pour mot | SOC War Room | Script 3min, chaque phrase cite une primitive Band |
| 3 decision paths + gouvernance | GateKeeper | Auto-approve / Human Review / Reject |
| Status page HTML stdlib | GateKeeper | Serveur HTTP 20 lignes, pas de dépendance |
| Offline path si API/WiFi fail | SOC War Room | `run_offline_demo.py` sans Band ni LLM |
| Fixtures JSON multi-scénarios | SOC War Room | 5 incidents, 5 résultats différents |
| Cross-framework visible | SOC War Room, Muster | Agents sur frameworks différents dans la même room |
| Stale replay dedup | War Room (TestStudent) | `_seen_ids` + `_cutoff` dans l'adapter |
| LLM concurrency slot gate | War Room | Évite les 429 sur appels parallèles |
| Feedback loop (agent se corrige) | SOC War Room, E-com | Review → dispute → rescope |
| One-liner anti-piège | SOC War Room | "Strip Band out = 5 scripts qui peuvent pas se parler" |
| Slide deck commité | SOC War Room | `.pptx` dans le repo |
| Business value metrics | SOC War Room | "118× faster, 5.2 analyst-hours saved" |
| BAND-API-CONTRACT.md | Muster | Documentation des findings API |

---

## Audit des fichiers

### À GARDER (Band-native, prêts)

| Fichier | Rôle | Statut |
|---------|------|--------|
| `agents/band_agent.py` | Base class SimpleAdapter | ✅ OK mais améliorer stale replay |
| `agents/keeper_band.py` | Keeper — fact store SQLite + auto-detect conflits | ✅ OK |
| `agents/reconciler_band.py` | Reconciler — LLM conflict resolver | ✅ OK |
| `agents/registry_band.py` | Registry — directory service | ✅ OK |
| `agents/scraper_band.py` | Scraper — git parsing → Keeper | ✅ OK |
| `agents/provider.py` | LLM provider (Featherless → OpenAI) | ✅ OK, ajouter concurrency gate |
| `agents/validation.py` | Pydantic models | ✅ OK |
| `agents/keeper.py` | **KeeperStore** class + ReconcilerStore | ⚠️ Garder classes, supprimer KeeperAgent HTTP |
| `agents/reconciler.py` | **ReconcilerStore** class + LLM helpers | ⚠️ Garder _llm_suggest etc. Supprimer ReconcilerAgent HTTP |
| `agents/registry.py` | **RegistryStore** class | ⚠️ Garder classe, supprimer RegistryAgent HTTP |
| `scripts/git_scraper.py` | Standalone git scraper | ⚠️ Refactor → poster dans Band room |

### À SUPPRIMER (morts pour le hackathon)

| Fichier | Raison |
|---------|--------|
| `agents/base.py` | Serveur Starlette HTTP — Band est le transport |
| `agents/auth.py` | Bearer + HMAC — Band gère l'auth |
| `agents/runner.py` | Lance 3 serveurs HTTP → remplacé par `run_mesh.sh` |
| `test_integration.py` | Test HTTP e2e → remplacer par scripts/ |
| `protocols/a2a.py` | JSON-RPC 2.0 — plus besoin |
| `protocols/json_parser.py` | provider.py le fait déjà |

---

## Plan de migration

### Phase 0 — Préparation (avant tout)

Créer la **room Band** et les **5 agents platform**:
1. Va sur https://app.band.ai → créer 5 agents: Scraper, Keeper, Reconciler, Registry, Bridge
2. Créer une room "Knowledge Mesh"
3. Ajouter les 5 agents + toi (humain) dans la room
4. Copier les 5 API keys dans `.env`

Structure `.env`:

```env
# ── Band credentials ──────────────────────────
BAND_SCRAPER_ID=
BAND_SCRAPER_KEY=
BAND_KEEPER_ID=
BAND_KEEPER_KEY=
BAND_RECONCILER_ID=
BAND_RECONCILER_KEY=
BAND_REGISTRY_ID=
BAND_REGISTRY_KEY=
BAND_BRIDGE_ID=
BAND_BRIDGE_KEY=
BAND_ROOM_ID=           # la room partagée
BAND_USER_HANDLE=        # mael2perso/mael

# ── LLM Provider ──────────────────────────────
FEATHERLESS_API_KEY=
FEATHERLESS_MODEL=Qwen/Qwen2.5-14B-Instruct
```

### Phase 1 — Architecture (30min)

```
Structure finale:

agents/noyau/
  band_agent.py          ← base SimpleAdapter amélioré (stale replay + mention routing)
  keeper_band.py         ← Keeper: SQLite facts + auto-detect conflit
  reconciler_band.py     ← Reconciler: LLM conflict resolver
  registry_band.py       ← Registry: annuaire
  scraper_band.py        ← Scraper: parse git → facts
  bridge_agent.py        ← Bridge: miroir room → dashboard (Nouveau)

dashboard/
  server.py              ← serveur HTTP stdlib (comme GateKeeper)
  index.html             ← status board (comme GateKeeper)

scripts/
  run_mesh.sh            ← lance les 5 agents Band
  run_offline_demo.py    ← démo sans Band ni LLM (inspiré SOC War Room)
  reset_db.py            ← vide les SQLite
  demo_trigger.sh        ← injecte un fixture dans la room

fixtures/                ← 4 scénarios de démo
  code_vs_doc.json       ← version conflict: pyproject.toml vs README.md
  merge_conflict.json    ← deux sources disent des choses différentes
  stale_data.json        ← doc dit X, code dit Y, doc est vieux
  clean_run.json         ← pas de conflit, tout est cohérent

DEMO.md                  ← script 3min pour le jury
```

**Améliorations clés à backporter dans `band_agent.py`:**

```python
# 1. Stale replay dedup (inspiré War Room TestStudent)
_seen_ids: set = set()
_cutoff = datetime.now() - timedelta(seconds=10)

# 2. Mention routing anti-loop
if msg.sender_type == "Agent":
    # Reply to human, not to the agent
    mentions = [os.getenv("BAND_USER_HANDLE")]
else:
    mentions = [msg.sender_name]

# 3. Éviter de réagir à ses propres messages
if msg.sender_id == self_agent_id:
    return
```

### Phase 2 — Bridge Agent (le dashboard en temps réel)

Inspiré de War Room (TestStudent) — 1 agent déterministe qui:

1. Écoute TOUS les messages de la room (pas de filtrage @mention)
2. Les stocke dans un buffer JSON visible par le dashboard
3. Expose `/events` et `/status` via HTTP

C'est la **seule** entité HTTP qui reste — un serveur stdlib minimal.

```python
# agents/bridge_agent.py — 150 lignes max
class BridgeAgent(SimpleAdapter):
    """Mirror room events to dashboard via HTTP. No LLM."""
    
    async def on_message(self, msg, tools, ...):
        append_to_buffer(msg)
    
    # + Simple HTTP server for dashboard polling
    # GET /events → return buffer
    # GET /status → {facts, conflicts, agents_status}
```

### Phase 3 — Dashboard (stdlib HTTP, 0 dépendance)

Inspiré de **GateKeeper**: un serveur HTTP Python stdlib (`http.server`) qui:

```
dashboard/
  server.py    ← 40 lignes: ThreadingHTTPServer + 3 routes
  index.html   ← page unique HTML+CSS+JS, polling JS
```

Le dashboard montre:

| Élément | Données | Source |
|---------|---------|--------|
| **Bandeau statut** | Agents up/down | Poll bridge `/status` |
| **Compteurs** | #{facts}, #{conflits ouverts}, #{résolus} | Bridge |
| **Timeline live** | Derniers messages Band | Bridge `/events` |
| **Dernier conflit** | Détail + score LLM + suggestion | Bridge |
| **Bouton APPROVE** | (optionnel) POST au bridge → injecte dans room | — |

Zéro dépendance. Même du HTML qui marche si le repo est cloné et `uv run python dashboard/server.py`.

### Phase 4 — Fixtures (4 scénarios de démo)

Inspiré de **SOC War Room** qui a 5 fichiers JSON avec des résultats différents.

```json
// fixtures/code_vs_doc.json
{
  "scenario": "Code vs Documentation — version conflict",
  "trigger": "Scraper scanne un repo, pyproject.toml dit 1.0.0, README.md dit 0.9.0",
  "expected": {
    "detected": true,
    "keeper_action": "@reconciler detect — 1 conflit",
    "reconciler_resolution": "Fact #12 gagnant (pyproject.toml = source of truth)",
    "severity": "LOW",
    "confidence": 0.95
  },
  "loop": "Tape @keeper recall <project> pour vérifier les faits stockés"
}
```

4 fixtures:
1. **code_vs_doc.json** — version conflict simple (LOW, auto-resolve)
2. **merge_conflict.json** — deux sources de code disent des choses contradictoires (MEDIUM, human review)
3. **stale_data.json** — doc vieille, code récent (HIGH, doc est fausse)
4. **clean_run.json** — tout cohérent (pas de conflit)

### Phase 5 — DEMO.md (script 3 minutes)

Inspiré de la **SOC War Room** — chaque phrase nomme une primitive Band.

Voir fichier séparé `DEMO.md`.

Structure du script:

```
0. Setup (avant la salle)
   Ouvrir dashboard → http://localhost:8765
   Ouvrir app.band.ai → room "Knowledge Mesh"

1. Hook (20s) — "Un problème de knowledge management..."
   "Deux sources disent la même chose différemment — normalement il faut un humain
    pour trancher. Regardez 4 agents le faire en 30 secondes via Band."

2. Live run (90s) — lancer run_mesh.sh
   Étape 1: Scraper → "Band permet à un agent déterministe de poster des faits
             structurés dans la room, que Keeper reçoit via @mention."
   Étape 2: Keeper détecte conflit → "SQL JOIN — Band transporte la donnée,
             Keeper fait l'analyse."
   Étape 3: Keeper @reconciler → "Band route le problème au bon spécialiste."
   Étape 4: Reconciler résout → "LLM + Band = résolution visible par tous."
   (Optionnel) Étape 5: Humain APPROVE → "Band garde la trace de la décision."

3. Dashboard payoff (30s) — "/ sur le dashboard"
   - Timeline: les messages Band en live
   - Status: faits stockés, conflits résolus
   - "Band n'est pas un bus — c'est le système nerveux. Chaque handoff,
    chaque décision, chaque @mention traverse Band."

4. Si Band / API / WiFi fail — offline path
   "python scripts/run_offline_demo.py fixtures/code_vs_doc.json"
   → idem sans Band ni LLM.

5. One-liner anti-piège:
   "Band is the coordination layer: @mention routing between specialists,
    shared context without shared memory, cross-framework agents in one room,
    and a human-in-the-loop gate. Strip Band out and you have 4 SQLite databases
    that can't find or talk to each other."
```

---

## Risques et mitigations

| Risque | Solution | Source |
|--------|----------|--------|
| Band API rate limits (429) | Espacer messages 0.5s + retry avec backoff | War Room |
| Stale replay au reconnect | `_seen_ids` + `_cutoff` dans adapter | War Room |
| 4 LLM calls simultanés | LLM slot gate (acquire/release) | War Room |
| Crédits Featherless épuisés | Offline path sans LLM | SOC War Room |
| Wi-Fi / Band down pendant démo | Offline path | SOC War Room |
| Jury comprend pas Band | DEMO.md + slides + one-liner | SOC War Room |

---

## Fichiers à créer

| Fichier | Source d'inspiration | Priorité |
|---------|----------------------|----------|
| `agents/bridge_agent.py` | War Room Bridge | 🔴 Haute |
| `dashboard/server.py` | GateKeeper server.py | 🔴 Haute |
| `dashboard/index.html` | GateKeeper / page | 🔴 Haute |
| `scripts/run_mesh.sh` | — | 🔴 Haute |
| `scripts/run_offline_demo.py` | SOC War Room | 🔴 Haute |
| `fixtures/*.json` (×4) | SOC War Room | 🟡 Moyenne |
| `DEMO.md` | SOC War Room | 🟡 Moyenne |
| `slides/` (ou .pptx) | SOC War Room | 🟢 Basse |

## Fichiers à supprimer (après migration)

```
agents/base.py
agents/auth.py
agents/runner.py
test_integration.py
protocols/a2a.py
protocols/json_parser.py
```

## Fichiers à refactor (garder classes métier, supprimer agents HTTP)

```
agents/keeper.py      → garder KeeperStore
agents/reconciler.py  → garder ReconcilerStore + _llm_* helpers
agents/registry.py    → garder RegistryStore
```

---

## Timeline

| Phase | Durée | Livrable |
|-------|-------|----------|
| 0 — Setup Band (room + 5 agents + .env) | 10min | Room prête |
| 1 — Architecture + band_agent.py amélioré | 30min | Stale replay + mention routing |
| 2 — Bridge Agent | 30min | Buffer HTTP pour dashboard |
| 3 — Dashboard (stdlib) | 30min | Status + timeline live |
| 4 — Fixtures ×4 | 20min | Scénarios de démo |
| 5 — run_mesh.sh + offline path | 20min | Lancement 1 cmd |
| 6 — DEMO.md + slides | 30min | Script 3min |
| **Total** | **~2h30** | Repo hackathon-ready |
