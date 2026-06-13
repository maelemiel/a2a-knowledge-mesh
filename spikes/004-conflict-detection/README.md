# 004: Conflict Detection

Valide que le Reconciler Agent peut détecter des contradictions entre 2 stores.

## Question

> Given two SQLite stores with facts, when the Reconciler compares them, does it correctly detect contradictions (same key, different value)?

## Résultat

Spike contenu dans `test_conflict.py` — pas de serveur, pure logique.

## Verdict

✅ VALIDATED
