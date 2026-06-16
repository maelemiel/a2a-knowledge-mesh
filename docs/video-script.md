# A2A Knowledge Mesh — Script Vidéo 5 minutes

## Vue d'ensemble

- **Durée :** 5 minutes (300 secondes)
- **Ton :** Dynamique, démonstration technique, rythme soutenu
- **Format :** Screen recording + voiceover (ou facecam en incrustation)
- **Musique :** Tech beat discret, s'arrête pendant les démos

---

## 0:00 — 0:30 : Intro + Problème (30 sec)

### Visuel
- Écran noir → logo A2A Knowledge Mesh qui apparaît (3 nœuds triangulaires)
- Transition vers split screen : code d'un côté, documentation de l'autre
- Les contradictions s'affichent en rouge (ex: doc dit "Next.js", code dit "React")

### Audio (script voix)
> **"On le sait tous : la documentation dit X, le code dit Y. Et personne ne le remarque… jusqu'à ce que ça casse en production."**
>
> **"On a construit trois agents qui découvrent, stockent et réconcilient la connaissance — dans Band. Pas de serveur HTTP, pas de JSON-RPC. Tout se passe dans les rooms Band."**
>
> **"Bienvenue dans A2A Knowledge Mesh."**

---

## 0:30 — 1:00 : Architecture (30 sec)

### Visuel
- Schéma animé des 3 agents qui apparaissent en triangle
- Registry (annuaire) → Keeper (base de faits) → Reconciler (détecteur de conflits + LLM)
- Flèches entre les agents montrant les interactions
- Band logo en arrière-plan montrant que tout se passe DANS Band

### Audio (script voix)
> **"L'architecture est simple : trois agents Band, un job chacun."**
>
> **"Le Registry — l'annuaire. Chaque agent publie sa carte de compétences, et les autres le découvrent."**
>
> **"Le Keeper — le cerveau. Il stocke des faits structurés : 'projet X utilise framework Y', avec source et timestamp."**
>
> **"Le Reconciler — le détective. Il détecte les contradictions et demande à une IA de les résoudre."**
>
> **"Et tout ça vit dans Band. Les agents s'écoutent via le SDK, répondent dans les rooms. Pas de HTTP entre eux."**

---

## 1:00 — 2:00 : Démo en Direct — Scraper + Faits (60 sec)

### Visuel
- Terminal : `uv run python scripts/git_scraper.py .`
- Le scraper tourne en direct : on le voit scanner pyproject.toml, README.md, .env.example
- Les faits extraits s'affichent lisiblement
- Transition vers la room Band : le Keeper reçoit les messages `store subject=...`
- Show les faits stockés avec `uv run python mesh.py status`

### Audio (script voix)
> **"On attaque le problème à la racine. On a un scraper Git qui scanne le projet."**
>
> **"Regardez : il parse automatiquement le pyproject.toml, le README, le .env.example — tout ce qui contient de la connaissance."**
>
> **"Chaque information devient un fait structuré : 'projet → version = 0.1.0', 'projet → framework = Next.js'. Et hop, envoyé au Keeper via Band."**
>
> **"Le Keeper les stocke dans SQLite, avec source et timestamp. On peut les rappeler avec un simple `mesh.py recall`."**
>
> **"Et là, le problème commence. Parce que la doc dit une chose, et le code en dit une autre…"**

---

## 2:00 — 3:00 : Détection de Conflit + AI Suggestion (60 sec)

### Visuel
- Terminal : `uv run python mesh.py detect`
- Ou bande-annonce de la room Band : `@reconciler detect`
- Les conflits s'affichent : "project-ALLY → framework = Next.js (source: docs) vs React (source: code repo)"
- On voit l'appel LLM : petite animation "AI thinking..." avec une icône Featherless
- La suggestion AI s'affiche : "✅ AI suggests React (fact #42) — Reason: pyproject.toml is the authoritative source for dependencies"
- Les colonnes AI dans la base reconciler.db s'affichent

### Audio (script voix)
> **"On lance la détection. Le Reconciler lit la base du Keeper et cherche les paires contradictoires : même sujet, même propriété, valeurs différentes."**
>
> **"Bingo : la doc dit 'Next.js', le pyproject.toml dit 'React'. Qui croire ? Plutôt que de deviner, on appelle l'IA."**
>
> **"Via Featherless AI, on envoie les deux faits avec leurs sources et timestamps. L'IA analyse, compare, et suggère."**
>
> **"Résultat : 'React' gagne, parce que pyproject.toml est la source d'autorité pour les dépendances, pas la doc."**
>
> **"Tout est tracé : le conflit, la suggestion AI, la raison. Zéro perte d'information."**

---

## 3:00 — 3:30 : Résolution dans Band (30 sec)

### Visuel
- Room Band : message du Reconciler qui @mentionne les agents sources
- Un humain tape : `resolve conflict-abc123 42 pyproject.toml is authoritative`
- La résolution s'affiche, le conflit passe en "résolu"
- `mesh.py status` montre le conflit fermé

### Audio (script voix)
> **"Un humain peut résoudre en un clic. Dans la room Band, il tape 'resolve', l'ID du conflit, et le gagnant."**
>
> **"Le Reconciler enregistre la résolution, le conflit passe en 'fermé'. Fin de l'histoire."**
>
> **"Mais on peut faire encore mieux : laisser l'IA résoudre toute seule."**

---

## 3:30 — 4:00 : Auto-Resolution sans Humain (30 sec)

### Visuel
- Mode auto-resolve : on montre `RECONCILER_AUTO_RESOLVE=true` dans .env
- Nouveau scan → conflit détecté → AI sure à 85% → auto-resolve
- Le conflit passe directement en "résolu" sans intervention humaine
- Message dans la room : "✅ Auto-resolved conflict-abc456 — AI confidence: 87%"

### Audio (script voix)
> **"En mode auto, si l'IA a suffisamment confiance — par exemple, quand une source est clairement plus fiable qu'une autre — le Reconciler résout tout seul."**
>
> **"Et ça continue, en boucle. Chaque commit, chaque mise à jour de doc déclenche une passe de réconciliation."**
>
> **"Pendant que l'équipe code, les agents gardent la connaissance cohérente."**

---

## 4:00 — 4:30 : Business Value (30 sec)

### Visuel
- Split screen : Memory Store (YC P26) logo d'un côté, A2A Knowledge Mesh de l'autre
- Tableau comparatif : "Sync passif" vs "Réconciliation active"
- YC RFS #5 "Company Brain" qui apparaît
- Graphique : temps économisé vs vérification manuelle

### Audio (script voix)
> **"YC RFS #5 : Company Brain. Tom Blomfield cherche exactement ça : un layer de connaissance partagée pour les agents."**
>
> **"Memory Store, YC P26, fait du sync passif — copier Slack, Gmail, Notion. Très bien."**
>
> **"Mais nous, on va plus loin : on ne copie pas. On détecte les contradictions. On les résout. On réconcilie activement."**
>
> **"Quand la doc dit X et le code dit Y, nos agents tranchent. Pas dans une semaine, pas après un meeting — maintenant."**

---

## 4:30 — 5:00 : Conclusion (30 sec)

### Visuel
- Logo A2A Knowledge Mesh + 3 agents
- Liens qui s'affichent : GitHub, lablab.ai, Featherless AI, Band
- QRCodes (ou URLs lisibles)
- Fond : noir → "Questions ?"

### Audio (script voix)
> **"A2A Knowledge Mesh : trois agents Band qui découvrent, stockent et réconcilient la connaissance. Pour que les docs et le code parlent le même langage."**
>
> **"Le code est open source sur GitHub. Venez voir, contribuer, ou nous piquer l'idée."**
>
> **"Merci à Featherless AI pour les crédits, à l'équipe Band pour le SDK, et à vous pour votre attention."**
>
> **"Des questions ?"**

---

## Annexes Techniques

### Éléments à préparer avant l'enregistrement

1. **Environnement de démo nettoyé** : `rm -rf data/` pour repartir de zéro
2. **Variables d'environnement** : BAND_AGENT_ID, BAND_API_KEY, FEATHERLESS_API_KEY
3. **Repo cible** : un projet qui a des contradictions doc/code (ex: projet avec pyproject.toml + docs/ qui disent des choses différentes)
4. **Rooms Band** : une room Keeper, une room Reconciler, prêtes à l'emploi
5. **Fonds d'écran/overlays** : les logos Band, Featherless, A2A en incrustation

### Contre-temps possibles

| Problème | Solution |
|----------|----------|
| LLM rate-limite | Avoir un fallback OpenAI, ou pré-enregistrer les réponses LLM |
| Band WebSocket déconnecté | `mesh.py start` relance les 3 agents |
| Aucun conflit dans le repo | Pré-insérer des faits contradictoires manuellement |
| Délai de réponse Band | Préparer des screenshots de fallback si besoin |

### Matériel recommandé

- Microphone : casque ou micro-cravate (pas le micro de l'ordi)
- Résolution d'écran : 1920×1080 (16:9)
- Terminal : fond sombre, police large (Fira Code, 16pt)
- Enregistrement : OBS Studio, 60fps, encodage H.264
