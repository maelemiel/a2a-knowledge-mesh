# Rapport d'Audit Technique : Analyse des Agents Band-Native Actuels

## 1. Résumé Exécutif
- Cet audit se concentre spécifiquement sur le code source et la cohérence technique des agents **Band-native** (`band_agent.py`, `keeper_band.py`, `reconciler_band.py`, `registry_band.py`, `scraper_band.py`) actuellement présents dans le projet.
- **Résultat global :** La base Band-native est étonnamment mature. Les fonctionnalités complexes de sécurité (anti-boucle de messages) et les intégrations de logique métier (SQLite + LLM) sont déjà en place. Quelques incohérences mineures de fallback et des dépendances de fichiers partagés ont été identifiées.

## 2. Audit Détaillé des Fichiers Band-Native

### A. Socle Commun (`agents/band_agent.py`)
- **Points forts :** 
  - Gestion automatique de la reconnexion et du cycle de vie du WebSocket via le SDK Band.
  - **Sécurité et anti-boucle :** Déduplication efficace des messages répliqués lors d'une reconnexion (*stale replay*) via un ensemble d'identifiants (`_seen_ids`) et un filtre temporel (`_cutoff`).
  - **Mention routing :** Redéfinition dynamique de `tools.send_message` pour rediriger les réponses vers l'utilisateur humain (`BAND_USER_HANDLE`) si l'émetteur original était un agent, ce qui évite les pings-pongs infinis d'agents.
- **Faiblesses/Pistes :** 
  - La valeur de `_cutoff` est figée à l'initialisation (L67) : `self._cutoff = datetime.now(timezone.utc) - timedelta(seconds=15)`. Si l'agent reste connecté plusieurs heures, le seuil de coupure temporelle ne glisse pas et devient obsolète. Il vaudrait mieux recalculer cette coupure dynamiquement lors de la réception de chaque message.

### B. Stockage de Faits (`agents/keeper_band.py`)
- **Points forts :**
  - Bonne gestion du stockage SQLite via la classe partagée `KeeperStore`.
  - Support de l'insertion par lot (`store-batch`) avec format JSON, crucial pour l'intégration avec le Scraper.
  - Détection automatique de conflits lancée après chaque écriture.
- **Incohérence détectée :**
  - **Ligne 107 :** En cas de détection de conflit lors d'un `store` simple, le handle du Reconciler utilisé comme fallback par défaut est `"reconciler"`.
  - **Ligne 141 :** Lors d'un `store-batch`, le fallback par défaut est `"mael2perso/reconciler"`.
  - *Action :* Harmoniser pour utiliser le même fallback (idéalement `"reconciler"` ou récupérer dynamiquement depuis l'annuaire).

### C. Résolution de Conflits (`agents/reconciler_band.py`)
- **Points forts :**
  - Chaîne d'analyse LLM complète (Qwen 2.5 14B sur Featherless) : filtrage sémantique, suggestion du vainqueur, calcul de sévérité/confiance pour auto-résolution et analyse de cause racine.
  - Stockage local des conflits et de leurs résolutions via `ReconcilerStore`.
- **Dépendance d'architecture :**
  - L'agent accède directement au fichier SQLite du Keeper (`self.keeper_db_path = keeper_db or ... /data/keeper.db`) pour faire ses requêtes de détection. Dans un environnement de production distribué (où le Keeper tournerait sur une autre machine), cela échouerait. Pour la démo locale du hackathon, cela reste acceptable et évite les surcoûts réseau.

### D. Enregistrement (`agents/registry_band.py`)
- **Points forts :**
  - Implémentation simple et propre des commandes `register`, `discover` et `list` s'appuyant sur la base SQLite `RegistryStore`.

### E. Collecteur de Données (`agents/scraper_band.py`)
- **Points forts :**
  - Extraction de faits structurés à partir de `pyproject.toml`, `package.json`, `Cargo.toml`, `README.md` et `.env.example`.
  - Envoi groupé des faits en un seul message `store-batch` à l'adresse de `BAND_KEEPER_HANDLE` pour minimiser les appels réseau.

## 3. Recommandations de Code et de Transition

| Priorité | Fichier | Type | Description / Action |
| :--- | :--- | :--- | :--- |
| **MOYENNE** | [band_agent.py](file:///home/mael/mael/Dev/band/a2a-knowledge-mesh/agents/band_agent.py#L67) | Optimisation | Rendre le calcul de `self._cutoff` dynamique (au moment de la réception du message dans `on_message` plutôt qu'au constructeur `__init__`) pour que la protection stale-replay reste active sur le long terme. |
| **MOYENNE** | [keeper_band.py](file:///home/mael/mael/Dev/band/a2a-knowledge-mesh/agents/keeper_band.py#L141) | Alignement | Harmoniser les fallbacks de mention du Reconciler (`"reconciler"` vs `"mael2perso/reconciler"`). |
| **BASSE** | [scraper_band.py](file:///home/mael/mael/Dev/band/a2a-knowledge-mesh/agents/scraper_band.py#L133) | Qualité | (Déjà résolu) Supprimer les préfixes `f` inutiles sur les chaînes constantes (ex: `f"dep-npm"`). |

## 4. Statut de la Migration
Le codebase contient actuellement tous les agents Band-native prêts à l'emploi. Le code A2A HTTP traditionnel n'est plus nécessaire au fonctionnement de ces agents mais sert encore de support pour les tests d'intégration.
