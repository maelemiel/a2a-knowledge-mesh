# A2A Knowledge Mesh — Slide Deck

## Slide 1 — Titre

**A2A Knowledge Mesh**
*3 agents qui découvrent, stockent et réconcilient la connaissance — dans Band.*

[Logo: trois nœuds triangulaires avec un "K" central]

Projet présenté au Band of Agents Hackathon — Juin 2026

---

## Slide 2 — Le Problème

**La doc dit X, le code dit Y.**

- Les équipes maintiennent documentation et code séparément
- Les contradictions passent inaperçues jusqu'à la production
- Résultat : bugs évitables, onboarding lent, réunions de sync

**Notre insight :** Si on donnait à des agents le pouvoir de détecter et résoudre ces contradictions automatiquement ?

---

## Slide 3 — Architecture

**3 agents A2A qui collaborent dans Band**

```
┌──────────┐    ┌──────────┐    ┌──────────────┐
│ Registry │───►│  Keeper  │◄──►│  Reconciler  │
│ (annuaire)│   │ (faits)  │    │ (conflits+AI)│
└──────────┘    └──────────┘    └──────────────┘
                     │                │
                     ▼                ▼
                SQLite facts    SQLite conflicts
```

Chaque agent = une responsabilité. Un discovery, un store, un resolver. Découverte via A2A Agent Cards. Coordination via Band SDK.

---

## Slide 4 — Démo Flow

**Scraper → Keeper → Reconciler → Résolution**

1. **Git Scraper** scanne le repo (pyproject.toml, README, .env.example → faits structurés)
2. **Keeper** stocke les faits dans SQLite avec source_id + timestamp
3. **Reconciler** détecte les contradictions via SQL JOIN, appelle le LLM
4. **Résolution** : humaine (commande `resolve` dans Band) ou auto (si confiance > seuil)

Tout se passe dans Band — les agents discutent dans les rooms, pas via HTTP.

---

## Slide 5 — AI en Action

**3 usages du LLM (Featherless AI)**

| Usage | Détail |
|-------|--------|
| **Extraction** | Extrait des faits (subject, predicate, object) depuis texte non structuré |
| **Détection sémantique** | Compare deux faits et détermine lequel est correct |
| **Auto-resolution** | Résout sans humain quand la confiance est suffisante |

Provider chain : Featherless AI (code `BOA26`) → OpenAI → fallback timestamp.

---

## Slide 6 — Stack Technique

| Couche | Technologie |
|--------|-------------|
| **Langage** | Python 3.11+ (uv pour la vitesse) |
| **Framework agent** | Band SDK (SimpleAdapter, WebSocket) |
| **Protocole** | A2A (Agent-to-Agent, Linux Foundation) |
| **Stockage** | SQLite (WAL mode, un fichier par agent) |
| **LLM** | Featherless AI + OpenAI fallback |
| **Validation** | Pydantic (JSON-RPC params) |
| **Auth** | Bearer tokens par rôle |

---

## Slide 7 — Business Value

**YC RFS #5 : Company Brain** — Tom Blomfield

> "Every company has a brain — a shared knowledge layer. We're building the agent-native version."

**Marché validé :** Memory Store (YC P26) a levé $3M+ pour du sync passif.

**Notre différenciation :**

| Memory Store | Nous |
|--------------|------|
| Sync passif (Slack → mémoire) | 🡪 Réconciliation **active** |
| Copie ce que les humains disent | 🡪 Détecte **les contradictions** |
| Pas d'auto-résolution | 🡪 LLM + auto-resolution autonome |

---

## Slide 8 — Résultats

**Ce qui marche aujourd'hui :**

- ✅ 3 agents Band opérationnels (Registry, Keeper, Reconciler)
- ✅ Git Scraper : extrait des faits de pyproject.toml, README, .env, docs/
- ✅ Détection de conflits via SQL JOIN (O(n log n))
- ✅ Suggestion AI via Featherless / OpenAI
- ✅ Résolution humaine ou automatique
- ✅ Provenance complète (source_id, timestamp, version)

**Détection typique :** `project-ALLY → framework = "Next.js"` (doc) vs `"React"` (code)

---

## Slide 9 — Roadmap

**Ce qui vient après le hackathon :**

| Phase | Fonctionnalité |
|-------|----------------|
| P1 | **Extraction IA** : LLM extrait des faits de fichiers non structurés (`.md`, `.txt`) |
| P2 | **Détection sémantique** : "port 8000" ≠ "port 8765" détecté comme conflit |
| P3 | **Auto-resolution configurable** : seuils de confiance par équipe |
| P4 | **Multi-repo** : scraper distribué, plusieurs Keepers |
| P5 | **Dashboard** : vue d'ensemble des conflits ouverts/résolus |

---

## Slide 10 — Merci + Liens

**Merci au jury du Band of Agents Hackathon !**

**Ressources :**
- GitHub : [github.com/maelemiel/a2a-knowledge-mesh](https://github.com/maelemiel/a2a-knowledge-mesh)
- Lablab.ai : [soumission lablab.ai]
- Vidéo démo : [lien vidéo 5 min]

**Technologies :** Band SDK · A2A Protocol · Python · SQLite · Featherless AI

**Questions ?**
