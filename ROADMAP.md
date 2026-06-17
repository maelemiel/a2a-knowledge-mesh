# ROADMAP — A2A Knowledge Mesh

> Ce qui peut / doit être construit ensuite. Chaque ligne est un agent
> source qui alimente le mesh en faits — le moteur de conflit + résolution
> IA existe déjà, il manque juste les extracteurs.

---

## Principe

Le mesh a déjà :
- ✅ Stockage RDF-lite (sujet, prédicat, objet, source)
- ✅ Détection de conflits par SQL JOIN
- ✅ Résolution IA (LLM score + suggestion)
- ✅ Dashboard + timeline
- ✅ Scraper repo (extraction de faits depuis le code)

**Ce qui manque :** des agents qui extraient des faits depuis d'autres sources
(GitHub, Jira, Linear, Slack, Teams...) et les poussent dans le Keeper.

Chaque nouvelle source = un nouveau fichier `agents/<source>_agent.py`
qui suit le pattern du Scraper :

```
1. Connects to source API
2. Extracts structured facts (subject, predicate, object, source_id)
3. Sends to Keeper via Band @mention
4. Keeper détecte automatiquement les contradictions avec les autres sources
```

---

## Tier 1 — Impact immédiat (2-3 jours chacun)

### 1. GitHub Issues Agent

**Pattern :** `agents/github_agent.py` — Band agent qui se connecte à l'API GitHub.

**Facts extraits :**

| Subject | Predicate | Object | Source |
|---------|-----------|--------|--------|
| `issue-123` | `title` | "Fix login bug" | `github/maelemiel/repo` |
| `issue-123` | `status` | `open` / `closed` | `github/maelemiel/repo` |
| `issue-123` | `priority` | `high` / `medium` / `low` | `github/maelemiel/repo` |
| `issue-123` | `assignee` | `@mael` | `github/maelemiel/repo` |
| `issue-123` | `milestone` | `v2.0` | `github/maelemiel/repo` |

**Conflits détectables :**
- Issue marquée `closed` mais aucun commit/PR lié → **ghost close**
- Issue marquée `high priority` mais sans assignee depuis 7 jours → **orphan critical**
- Deux issues ouvertes avec le même titre → **duplicate**
- Issue `status=done` mais PR associée `status=draft` → **premature celebration**

**Commandes Band :**
```
@github scan maelmiel/a2a-knowledge-mesh
@github issue-123 status
@github watch maelmiel/ally  # watch mode: poll every 5 min
```

**API :** GitHub REST v3 (no auth needed for public repos) + GraphQL v4
**Effort :** ~2 jours (pattern identical to Scraper)

---

### 2. Linear Agent

**Pattern :** `agents/linear_agent.py` — idem mais pour Linear.

**Facts extraits :**

| Subject | Predicate | Object |
|---------|-----------|--------|
| `LIN-123` | `title` | "Add user auth" |
| `LIN-123` | `status` | `In Progress` |
| `LIN-123` | `priority` | `High` |
| `LIN-123` | `assignee` | `mael` |
| `LIN-123` | `estimate` | `5` (points) |
| `LIN-123` | `cycle` | `C23` |

**Conflits spécifiques :**
- Issue en `Done` mais cycle pas terminé → **cycle inconsistency**
- Deux issues avec `estimate=13+` jamais décomposées → **missing breakdown**
- Issue en `Backlog` avec `priority=Urgent` → **zombie urgent**

**API :** Linear GraphQL API (API key dans .env)
**Effort :** ~2 jours

---

### 3. Jira Agent

**Pattern :** `agents/jira_agent.py`

Même concept. Conflits clés :
- Ticket `status=Closed` mais `fixVersion` pas release → **version drift**
- Ticket `status=In Progress` mais `assignee` absent → **ownerless work**
- Sprint passé mais tickets encore `In Progress` → **scope creep**

**API :** Jira REST v3 (email + token)
**Effort :** ~2 jours

---

### 4. Slack / Teams "Decision Capture" Agent

**Pattern :** `agents/slack_agent.py` ou webhook → injecteur de faits.

Le problème : les décisions prises en réunion/DM n'atterrissent jamais
dans le ticketing. Solution : un agent dans un channel qui écoute et
extrait les décisions.

**Facts extraits :**
```
"On prend Supabase pour l'auth"
→ subject=project-ALLY predicate=auth-provider object=Supabase source=slack

"Je livre la PR vendredi"
→ subject=PR-42 predicate=deadline object=2026-06-20 source=slack

"Plus besoin du module X"
→ subject=project-ALLY predicate=module-X object=deprecated source=slack
```

**Conflits détectables :**
- Décision Slack "on prend Supabase" mais issue tracker dit `Firebase` → **decision gap**
- "Je livre vendredi" mais issue en `Backlog` → **delivery vs reality**
- Personne n'a répondu "oui" à la question → **unconfirmed decision**

**Deux approches :**
- **Push** — Slack/Teams webhook → endpoint HTTP (bridge agent) → Keeper
- **Poll** — Agent qui lit l'historique du channel périodiquement

**Effort :** ~3 jours (selon complexité du NLP de décision)

---

## Tier 2 — Company Brain complet (1-2 semaines chacun)

### 5. PR / Code Review Agent

**Pattern :** `agents/pr_agent.py`

**Problème :** Les PR descriptions racontent une histoire, le code en
raconte une autre. Personne ne vérifie la cohérence.

**Facts extraits :**
| Subject | Predicate | Object | Source |
|---------|-----------|--------|--------|
| `PR-42` | `title` | "Add auth middleware" | `github` |
| `PR-42` | `files-changed` | `12` | `github` |
| `PR-42` | `additions` | `340` | `github` |
| `PR-42` | `deletions` | `20` | `github` |
| `PR-168` | `description-claims` | "fix login bug" | `github` |
| `PR-168` | `actual-changes` | "refactored colors" | `github` (LLM diff analysis) |

**Conflits détectables :**
- PR dit "fix login bug" mais le diff ne touche que du CSS → **description mismatch**
- PR modifie l'API mais n'a pas de test → **missing coverage**
- PR ajoute une dépendance mais n'update pas `pyproject.toml` → **missing dep declaration**

**Key insight :** Le LLM analyse le diff et compare à la description.
C'est exactement le pattern Scraper mais appliqué à un PR.

---

### 6. Post-Mortem / Incident Agent

**Pattern :** `agents/incident_agent.py`

**Problème :** Les post-mortems génèrent des action items qui sont
oubliés 2 semaines plus tard.

**Facts extraits :**
| Subject | Predicate | Object |
|---------|-----------|--------|
| `incident-2026-06-15` | `severity` | `SEV1` |
| `incident-2026-06-15` | `action-item` | "Add rate limiting" |
| `incident-2026-06-15` | `action-item-status` | `open` |
| `incident-2026-06-15` | `root-cause` | "Missing timeout" |
| `action-rate-limiting` | `linked-issue` | `LIN-456` |

**Conflits :**
- Action item dit `resolved` mais issue toujours `open` → **unclosed loop**
- Même root cause apparaît dans 3 incidents différents → **systemic issue ignored**
- Action item sans assignee depuis 30 jours → **abandoned fix**

---

### 7. Dependency / Config Drift Agent

**Pattern :** `agents/dep_agent.py`

**Problème :** pyproject.toml dit Python 3.11, Dockerfile dit Python 3.9,
CI utilise Python 3.12. Qui croire ?

**Sources :**
- `pyproject.toml` (requires-python, dependencies)
- `Dockerfile` (FROM python:X)
- `.github/workflows/*.yml` (setup-python version)
- `Dockerfile` (pip install vs poetry)
- README.md (setup instructions)

**Conflits détectables :**
- `pyproject.toml` requiert Python ≥3.11 mais Dockerfile utilise `python:3.9-slim`
- README dit `pip install` mais le projet utilise `uv`
- CI teste sur ubuntu-latest mais le Dockerfile est alpine

**Effort :** ~2 jours (déjà 80% fait via le Scraper actuel)

---

### 8. Compliance / Regulatory Agent

**Pattern :** `agents/compliance_agent.py`

**Problème :** Les régulations (RGPD, SOC2, HIPAA) listent des exigences.
Le code doit les refléter. Qui vérifie ?

**Facts extraits :**
| Subject | Predicate | Object |
|---------|-----------|--------|
| `RGAA-5.1` | `description` | "Chaque média temporel doit avoir une transcription" |
| `RGAA-5.1` | `status` | `not-implemented` |
| `feature-login` | `has-captcha` | `false` |
| `feature-login` | `has-rate-limit` | `false` |

**Conflits :**
- RGPD dit "delete user data on request" mais aucune API DELETE user → **compliance gap**
- SOC2 dit "audit logging required" mais projet n'a pas de logger → **missing control**

---

## Tier 3 — Infrastructure Mesh (quand le mesh tourne pour de vrai)

### 9. Webhook Gateway Agent

Un agent central qui reçoit des webhooks HTTP (GitHub push, Linear issue update,
Slack message, Jira transition) et les transforme en faits.

**Remplace le polling** par du push. Critère pour passer de "demo" à "production".

```
GitHub push ──┐
Linear update─┼─► Webhook Gateway ──► Keeper (store fact)
Slack msg ────┘
```

### 10. Fact Subscription / Watch Agent

Un agent qui permet de dire "watch subject=project-ALLY predicate=framework"
et reçoit une notification Band quand un conflit apparaît ou qu'un fait
change. L'inverse du polling dashboard.

### 11. Fact Export / Sync Agent

Exporter les faits vers :
- Notion (documentation auto-générée)
- Markdown dans le repo (README auto-à-jour)
- Un graphique knowledge graph interactif (Neo4j ou D3.js)

---

## Résumé — Priorité

```
Tier 1 (2-3j)          Tier 2 (1-2 sem)         Tier 3 (quand stable)
─────────────────      ──────────────────       ──────────────────
GitHub Issues Agent    PR Review Agent           Webhook Gateway
Linear Agent           Post-Mortem Agent         Subscription Agent
Jira Agent             Dependency Drift          Fact Export
Slack/Teams Agent      Compliance Agent          Knowledge Graph Viz
```

**Ma recommandation :** commencer par **GitHub Issues Agent** (le plus
universel, tout le monde comprend le problème des issues) puis **Slack
Decision Capture** (le plus impressionnant en démo — l'agent qui écoute
une conversation et détecte les décisions non-loguées).
