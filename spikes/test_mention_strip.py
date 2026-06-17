"""Smoke test for _strip_mentions — all formats including @[[UUID]]."""

import sys
sys.path.insert(0, ".")

from agents.band_agent import _strip_mentions

TESTS = [
    # (raw, sender_name, expected)
    # ── Simple single-word mention
    ("@scraper scan self", "Maël Perrigaud", "scan self"),
    ("@keeper store subject=X", "Maël Perrigaud", "store subject=X"),
    # ── Display name with spaces + /agent
    ("@Maël Perrigaud/scraper scan self", "Maël Perrigaud", "scan self"),
    ("@Maël Perrigaud/keeper recall ALLY", "Maël Perrigaud", "recall ALLY"),
    # ── Display name with spaces, no agent
    ("@Maël Perrigaud status", "Maël Perrigaud", "status"),
    # ── Unknown sender + /agent (regex fallback)
    ("@Maël Perrigaud/scraper scan self", "", "scan self"),
    # ── Unknown sender single word
    ("@scraper scan self", "", "scan self"),
    # ── Multiple mentions
    ("@scraper @keeper store subject=X", "", "store subject=X"),
    # ── No mention (passthrough)
    ("scan self", "Maël Perrigaud", "scan self"),
    ("store subject=X predicate=Y", "", "store subject=X predicate=Y"),
    # ── Empty / mention only
    ("", "", ""),
    ("@scraper", "", ""),
    ("@Maël Perrigaud/scraper", "Maël Perrigaud", ""),
    # ── Accent
    ("@Maël Perrigaud/scraper detect", "Maël Perrigaud", "detect"),
    # ── Sender mismatch + /agent
    ("@Jean Dupont/scraper scan self", "Maël Perrigaud", "scan self"),
    # ── NEW: Band @[[UUID]] format
    ("@[[90f19aa6-3ec8-4871-a572-d694d2a1893b]] scan self", "Maël Perrigaud", "scan self"),
    ("@[[90f19aa6-3ec8-4871-a572-d694d2a1893b]] @[[abc-123]] detect", "", "detect"),
    ("@[[deadbeef-dead-beef-dead-beefdeadbeef]]", "", ""),
    # ── Mix: UUID + display name
    ("@[[uuid]] @scraper scan self", "", "scan self"),
    # ── Mid-content mention preserved
    ("scan self @scraper", "Maël Perrigaud", "scan self @scraper"),
]

failed = 0
for raw, sender, expected in TESTS:
    result = _strip_mentions(raw, sender)
    status = "OK" if result == expected else "FAIL"
    if result != expected:
        failed += 1
        print(f"{status} raw={raw!r} sender={sender!r}")
        print(f"   expected={expected!r}")
        print(f"   got     ={result!r}")

print(f"\n{failed}/{len(TESTS)} failed")
sys.exit(0 if failed == 0 else 1)
