#!/usr/bin/env python3
"""
mesh — CLI for A2A Knowledge Mesh (Band-native).

Sends commands through Band API instead of local HTTP.
Also provides direct DB inspection for debug.

Usage:
  mesh send "store subject=X predicate=Y object=Z source=docs"  → sends to Keeper via Band
  mesh recall <subject>                                           → reads Keeper DB directly
  mesh detect                                                     → reads Keeper DB, detect conflicts
  mesh status                                                     → reads all DBs, show state
  mesh graphify [output_path]                                    → generate visual knowledge graph HTML
  mesh start                                                      → launch all 4 Band agents
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

ROOT = Path(__file__).parent
DATA = ROOT / "data"

BAND_API_KEY = os.getenv("BAND_API_KEY", "")
BAND_BASE_URL = os.getenv("BAND_BASE_URL", "https://app.band.ai")
BAND_AGENT_ID = os.getenv("BAND_AGENT_ID", "")


# ---------------------------------------------------------------------------
# Direct DB inspection (no Band connection needed)
# ---------------------------------------------------------------------------


def _db(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    return sqlite3.connect(str(path))


def cmd_recall(subject: str = "") -> None:
    conn = _db(DATA / "keeper.db")
    if conn is None:
        print("Keeper DB not found. Start agents first.")
        return
    if subject:
        rows = conn.execute(
            "SELECT id, subject, predicate, object, source_id, timestamp "
            "FROM facts WHERE subject=? ORDER BY timestamp DESC", (subject,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, subject, predicate, object, source_id, timestamp "
            "FROM facts ORDER BY timestamp DESC LIMIT 30"
        ).fetchall()
    conn.close()

    if not rows:
        print("No facts.")
        return
    for r in rows:
        print(f"  #{r[0]} [{r[4]}] {r[1]} → {r[2]} = {r[3]}  ({time.ctime(r[5])})")


def cmd_detect() -> None:
    conn = _db(DATA / "keeper.db")
    if conn is None:
        print("Keeper DB not found.")
        return
    rows = conn.execute("""
        SELECT f1.subject, f1.predicate,
               f1.id, f1.object, f1.source_id,
               f2.id, f2.object, f2.source_id
        FROM facts f1
        JOIN facts f2 ON f1.subject = f2.subject
                     AND f1.predicate = f2.predicate
                     AND f1.source_id < f2.source_id
                     AND f1.object != f2.object
    """).fetchall()
    conn.close()

    if not rows:
        print("✅ No conflicts.")
        return
    print(f"⚠️ {len(rows)} conflict(s):")
    for r in rows:
        print(f"  {r[0]} ({r[1]}): #{r[2]} ({r[4]}) vs #{r[5]} ({r[7]})")


def cmd_status() -> None:
    # Keeper DB
    conn = _db(DATA / "keeper.db")
    if conn:
        count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        subjects = conn.execute(
            "SELECT DISTINCT subject FROM facts ORDER BY subject"
        ).fetchall()
        conn.close()
        print(f"📦 Keeper: {count} facts, {len(subjects)} subjects")
        for s in subjects:
            print(f"     • {s[0]}")
    else:
        print("📦 Keeper: not started")

    # Reconciler DB
    conn = _db(DATA / "reconciler.db")
    if conn:
        open_c = conn.execute(
            "SELECT COUNT(*) FROM conflicts WHERE status='open'"
        ).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM conflicts"
        ).fetchone()[0]
        conn.close()
        print(f"⚡ Reconciler: {open_c} open / {total} total conflicts")
    else:
        print("⚡ Reconciler: not started")

    # Registry DB
    conn = _db(DATA / "registry.db")
    if conn:
        agents = conn.execute("SELECT name, skills FROM agents").fetchall()
        conn.close()
        print(f"📋 Registry: {len(agents)} registered agent(s)")
        for a in agents:
            try:
                skills = json.loads(a[1])
            except (json.JSONDecodeError, TypeError):
                skills = [a[1]]
            print(f"     • {a[0]} — {', '.join(skills)}")
    else:
        print("📋 Registry: not started")


def cmd_send_via_band(content: str) -> None:
    """Send a command to Keeper via Band REST API."""
    if not BAND_API_KEY or not BAND_AGENT_ID:
        print("⚠️ BAND_API_KEY and BAND_AGENT_ID required to send via Band.")
        print("   Set them in .env or use 'recall'/'detect' for local DB queries.")
        return

    import httpx

    # Find Keeper in contacts/rooms or use predefined room
    room_id = os.getenv("BAND_KEEPER_ROOM_ID", "")
    if not room_id:
        print("⚠️ BAND_KEEPER_ROOM_ID not set. Set the room ID where Keeper listens.")
        return

    payload = {
        "message": {
            "content": content,
        }
    }
    headers = {
        "Authorization": f"Bearer {BAND_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{BAND_BASE_URL}/api/v2/agents/{BAND_AGENT_ID}/rooms/{room_id}/messages"

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=10)
        if resp.is_success:
            print(f"✅ Message sent via Band: {content[:80]}")
        else:
            print(f"❌ Band API error ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Failed to send: {e}")


def cmd_start() -> None:
    """Launch all 3 Band agents as subprocesses.

    Each agent reads its own credentials from .env:
      BAND_REGISTRY_ID/KEY  → registry_band
      BAND_KEEPER_ID/KEY    → keeper_band
      BAND_RECONCILER_ID/KEY → reconciler_band
    """
    import subprocess

    missing = []
    for var in ["BAND_REGISTRY_ID", "BAND_KEEPER_ID", "BAND_RECONCILER_ID", "BAND_SCRAPER_ID"]:
        if not os.getenv(var):
            missing.append(var)
    if missing:
        print(f"⚠️ Missing env vars: {', '.join(missing)}")
        print("   Set them in .env before starting agents.")
        return

    agent_names = ["registry_band", "keeper_band", "reconciler_band", "scraper_band"]
    procs = []
    for name in agent_names:
        p = subprocess.Popen(
            [sys.executable, "-m", f"agents.{name}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        procs.append(p)
        print(f"[{name}] started (pid={p.pid})")

    try:
        import select
        buffers = {p.stdout: b"" for p in procs if p.stdout}
        while procs:
            # Clean up dead processes
            procs = [p for p in procs if p.poll() is None]
            if not procs and not any(buffers.values()):
                break
            readable, _, _ = select.select(
                [f for f in buffers if f and not f.closed], [], [], 0.5
            )
            for f in readable:
                try:
                    chunk = f.read1(4096)
                    if chunk:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                except Exception:
                    pass
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()
    except ImportError:
        # Fallback: read sequentially
        for p in procs:
            if p.stdout:
                for line in p.stdout:
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Knowledge Mesh — Interactive Graph</title>
    <!-- Modern Google Font: Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <!-- Vis Network CSS & JS -->
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        :root {
            --bg: #0b0f19;
            --sidebar-bg: rgba(17, 24, 39, 0.85);
            --card-bg: rgba(31, 41, 55, 0.4);
            --border: rgba(55, 65, 81, 0.6);
            --border-glow: rgba(59, 130, 246, 0.5);
            --text: #f3f4f6;
            --text-muted: #9ca3af;
            --accent: #3b82f6;
            --accent-glow: rgba(59, 130, 246, 0.35);
            --success: #10b981;
            --warning: #f59e0b;
            --error: #ef4444;
            --error-glow: rgba(239, 68, 68, 0.3);
            --font-main: 'Inter', system-ui, -apple-system, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: var(--font-main);
            background-color: var(--bg);
            color: var(--text);
            height: 100vh;
            overflow: hidden;
            display: flex;
        }

        #sidebar {
            width: 380px;
            background: var(--sidebar-bg);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            z-index: 10;
            box-shadow: 10px 0 30px rgba(0, 0, 0, 0.5);
            transition: all 0.3s ease;
        }

        #header {
            padding: 24px;
            border-bottom: 1px solid var(--border);
            background: linear-gradient(135deg, rgba(59, 130, 246, 0.1) 0%, rgba(0,0,0,0) 100%);
        }

        #header h1 {
            font-size: 1.4rem;
            font-weight: 700;
            letter-spacing: -0.025em;
            margin-bottom: 6px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        #header h1 span {
            color: var(--accent);
            text-shadow: 0 0 15px var(--border-glow);
        }

        #header p {
            font-size: 0.8rem;
            color: var(--text-muted);
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            padding: 16px 24px;
            border-bottom: 1px solid var(--border);
        }

        .stat-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px;
            text-align: center;
        }

        .stat-card .val {
            font-size: 1.2rem;
            font-weight: 700;
            color: var(--text);
        }

        .stat-card .val.error {
            color: var(--error);
            text-shadow: 0 0 10px rgba(239, 68, 68, 0.4);
        }

        .stat-card .val.success {
            color: var(--success);
        }

        .stat-card .lbl {
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-top: 4px;
        }

        .sidebar-content {
            flex: 1;
            overflow-y: auto;
            padding: 20px 24px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .section-title {
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.075em;
            color: var(--text-muted);
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .conflict-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .conflict-item {
            background: rgba(239, 68, 68, 0.08);
            border: 1px solid rgba(239, 68, 68, 0.2);
            border-radius: 8px;
            padding: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .conflict-item:hover {
            background: rgba(239, 68, 68, 0.15);
            border-color: var(--error);
            box-shadow: 0 0 12px var(--error-glow);
        }

        .conflict-item .title {
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .conflict-item .title .warn-icon {
            color: var(--warning);
        }

        .conflict-item .desc {
            font-size: 0.75rem;
            color: var(--text-muted);
            font-family: var(--font-mono);
            background: rgba(0, 0, 0, 0.2);
            padding: 4px 6px;
            border-radius: 4px;
            margin-top: 6px;
            line-height: 1.3;
        }

        .search-box {
            position: relative;
        }

        .search-input {
            width: 100%;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 12px;
            color: var(--text);
            font-family: var(--font-main);
            font-size: 0.85rem;
            transition: all 0.2s ease;
        }

        .search-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 10px var(--accent-glow);
        }

        #details-panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
            display: none;
            animation: fadeIn 0.3s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(5px); }
            to { opacity: 1; transform: translateY(0); }
        }

        #details-panel h3 {
            font-size: 0.9rem;
            font-weight: 600;
            margin-bottom: 12px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 8px;
        }

        .detail-row {
            margin-bottom: 10px;
            font-size: 0.8rem;
        }

        .detail-row:last-child {
            margin-bottom: 0;
        }

        .detail-label {
            color: var(--text-muted);
            font-weight: 500;
            margin-bottom: 2px;
        }

        .detail-value {
            font-family: var(--font-mono);
            word-break: break-all;
            background: rgba(0, 0, 0, 0.15);
            padding: 4px 6px;
            border-radius: 4px;
            color: var(--text);
        }

        #network-container {
            flex: 1;
            height: 100%;
            position: relative;
            background: radial-gradient(circle at 50% 50%, #111827 0%, #030712 100%);
        }

        #network {
            width: 100%;
            height: 100%;
        }

        .controls {
            position: absolute;
            bottom: 24px;
            right: 24px;
            display: flex;
            gap: 10px;
            z-index: 5;
        }

        .btn-ctrl {
            background: rgba(17, 24, 39, 0.8);
            border: 1px solid var(--border);
            backdrop-filter: blur(8px);
            color: var(--text);
            width: 40px;
            height: 40px;
            border-radius: 8px;
            display: flex;
            justify-content: center;
            align-items: center;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }

        .btn-ctrl:hover {
            border-color: var(--accent);
            color: var(--accent);
            box-shadow: 0 0 10px var(--accent-glow);
        }

        .legend {
            position: absolute;
            top: 24px;
            right: 24px;
            background: rgba(17, 24, 39, 0.8);
            border: 1px solid var(--border);
            backdrop-filter: blur(8px);
            border-radius: 8px;
            padding: 12px;
            font-size: 0.75rem;
            z-index: 5;
            display: flex;
            flex-direction: column;
            gap: 6px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .legend-color {
            width: 12px;
            height: 12px;
            border-radius: 3px;
        }

        .empty-state {
            color: var(--text-muted);
            font-size: 0.8rem;
            font-style: italic;
            text-align: center;
            padding: 20px;
        }
    </style>
</head>
<body>
    <div id="sidebar">
        <div id="header">
            <h1>A2A <span>Knowledge Mesh</span></h1>
            <p>Interactive Drift &amp; Conflict Graph</p>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="val" id="stat-subjects">0</div>
                <div class="lbl">Subjects</div>
            </div>
            <div class="stat-card">
                <div class="val" id="stat-facts">0</div>
                <div class="lbl">Facts</div>
            </div>
            <div class="stat-card">
                <div class="val error" id="stat-conflicts">0</div>
                <div class="lbl">Conflicts</div>
            </div>
        </div>

        <div class="sidebar-content">
            <div class="search-box">
                <input type="text" class="search-input" id="search-input" placeholder="Search subject or object...">
            </div>

            <div id="details-panel">
                <h3 id="detail-title">Fact Details</h3>
                <div id="detail-content"></div>
            </div>

            <div>
                <div class="section-title">
                    <span>Detected Conflicts ⚠️</span>
                </div>
                <div class="conflict-list" id="conflict-list">
                    <!-- Conflicts populated here -->
                </div>
            </div>
        </div>
    </div>

    <div id="network-container">
        <div id="network"></div>

        <div class="legend">
            <div class="legend-item">
                <div class="legend-color" style="background-color: #3b82f6; border: 1.5px solid #2563eb;"></div>
                <span>Subject Node</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background-color: #10b981; border: 1.5px solid #059669;"></div>
                <span>Resolved/Valid Object</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background-color: #ef4444; border: 1.5px solid #dc2626; box-shadow: 0 0 8px rgba(239, 68, 68, 0.5);"></div>
                <span>Conflicting Object ⚠️</span>
            </div>
        </div>

        <div class="controls">
            <div class="btn-ctrl" id="btn-zoom-in" title="Zoom In">+</div>
            <div class="btn-ctrl" id="btn-zoom-out" title="Zoom Out">-</div>
            <div class="btn-ctrl" id="btn-fit" title="Fit Graph">⛶</div>
        </div>
    </div>

    <script type="text/javascript">
        const nodesData = {{NODES_JSON}};
        const edgesData = {{EDGES_JSON}};
        const conflictsData = {{CONFLICTS_JSON}};

        const container = document.getElementById('network');
        const data = {
            nodes: new vis.DataSet(nodesData),
            edges: new vis.DataSet(edgesData)
        };

        const options = {
            nodes: {
                font: {
                    color: '#f3f4f6',
                    face: 'Inter',
                    size: 14
                },
                shadow: {
                    enabled: true,
                    color: 'rgba(0,0,0,0.5)',
                    size: 6,
                    x: 2,
                    y: 2
                }
            },
            edges: {
                font: {
                    color: '#9ca3af',
                    face: 'Inter',
                    size: 11,
                    background: '#0b0f19',
                    strokeWidth: 0
                },
                arrows: {
                    to: {
                        enabled: true,
                        scaleFactor: 0.8
                    }
                },
                smooth: {
                    type: 'cubicBezier',
Roundness: 0.4
                }
            },
            physics: {
                solver: 'forceAtlas2Based',
                forceAtlas2Based: {
                    gravitationalConstant: -50,
                    centralGravity: 0.01,
                    springLength: 100,
                    springConstant: 0.08,
                    damping: 0.4,
                    avoidOverlap: 0.8
                },
                stabilization: {
                    enabled: true,
                    iterations: 150,
                    updateInterval: 25
                }
            },
            interaction: {
                hover: true,
                tooltipDelay: 200
            }
        };

        const network = new vis.Network(container, data, options);

        document.getElementById('stat-subjects').textContent = nodesData.filter(n => n.nodeType === 'subject').length;
        document.getElementById('stat-facts').textContent = edgesData.length;
        document.getElementById('stat-conflicts').textContent = Object.keys(conflictsData).length;

        const conflictList = document.getElementById('conflict-list');
        if (Object.keys(conflictsData).length === 0) {
            conflictList.innerHTML = '<div class="empty-state">No conflicts detected.</div>';
        } else {
            for (const [key, facts] of Object.entries(conflictsData)) {
                const [subject, predicate] = key.split('::');
                const item = document.createElement('div');
                item.className = 'conflict-item';
                
                const descLines = facts.map(f => `${f.source_id}: ${f.object}`).join('\n');
                
                item.innerHTML = `
                    <div class="title"><span class="warn-icon">⚠️</span> ${subject}</div>
                    <div class="detail-label" style="font-size:0.7rem;margin-top:2px;">predicate: ${predicate}</div>
                    <div class="desc" style="white-space: pre-line;">${descLines}</div>
                `;
                
                item.addEventListener('click', () => {
                    const subNodeId = 's:' + subject;
                    const neighborIds = facts.map(f => `o:${subject}:${predicate}:${f.object}`);
                    const focusIds = [subNodeId, ...neighborIds];
                    
                    network.fit({
                        nodes: focusIds,
                        animation: {
                            duration: 800,
                            easingFunction: 'easeInOutQuad'
                        }
                    });
                    
                    network.selectNodes([subNodeId]);
                    showNodeDetails(subNodeId);
                });
                
                conflictList.appendChild(item);
            }
        }

        network.on("click", function (params) {
            if (params.nodes.length > 0) {
                showNodeDetails(params.nodes[0]);
            } else if (params.edges.length > 0) {
                showEdgeDetails(params.edges[0]);
            } else {
                document.getElementById('details-panel').style.display = 'none';
            }
        });

        function showNodeDetails(nodeId) {
            const node = nodesData.find(n => n.id === nodeId);
            if (!node) return;

            const panel = document.getElementById('details-panel');
            const title = document.getElementById('detail-title');
            const content = document.getElementById('detail-content');

            panel.style.display = 'block';

            if (node.nodeType === 'subject') {
                title.innerHTML = `Subject: <span style="color: var(--accent);">${node.label}</span>`;
                const connectedEdges = edgesData.filter(e => e.from === nodeId);
                let html = `<div class="detail-row"><div class="detail-label">Total relations</div><div class="detail-value">${connectedEdges.length}</div></div>`;
                
                html += `<div style="margin-top: 10px; font-size: 0.75rem; font-weight:600; color: var(--text-muted);">Relationships:</div>`;
                html += `<div style="max-height: 150px; overflow-y: auto; margin-top: 4px; display:flex; flex-direction:column; gap:4px;">`;
                connectedEdges.forEach(e => {
                    const toNode = nodesData.find(n => n.id === e.to);
                    const isConflict = toNode && toNode.group === 'conflict_object';
                    const colorStyle = isConflict ? 'color: var(--error); font-weight:600;' : '';
                    html += `<div style="background: rgba(0,0,0,0.15); padding: 4px 6px; border-radius: 4px; display:flex; justify-content:space-between;">
                        <span>${e.label}</span>
                        <span style="${colorStyle}">→ ${toNode ? toNode.label : ''}</span>
                    </div>`;
                });
                html += `</div>`;
                
                content.innerHTML = html;
            } else {
                title.innerHTML = `Value Node: <span style="color: ${node.group === 'conflict_object' ? 'var(--error)' : 'var(--success)'};">${node.label}</span>`;
                
                let html = `
                    <div class="detail-row">
                        <div class="detail-label">Subject</div>
                        <div class="detail-value">${node.subject}</div>
                    </div>
                    <div class="detail-row">
                        <div class="detail-label">Predicate</div>
                        <div class="detail-value">${node.predicate}</div>
                    </div>
                `;
                
                if (node.facts && node.facts.length > 0) {
                    html += `<div style="margin-top:10px; font-size:0.75rem; font-weight:600; color: var(--text-muted);">Sources:</div>`;
                    node.facts.forEach(f => {
                        const dateStr = new Date(f.timestamp * 1000).toLocaleString();
                        html += `
                            <div style="background: rgba(0,0,0,0.15); padding: 8px; border-radius: 6px; margin-top: 4px; font-size: 0.75rem;">
                                <div><strong>Source ID:</strong> ${f.source_id}</div>
                                <div><strong>Source Type:</strong> ${f.source_type}</div>
                                <div><strong>Version:</strong> ${f.version}</div>
                                <div style="font-size:0.65rem; color: var(--text-muted); margin-top: 2px;">${dateStr}</div>
                            </div>
                        `;
                    });
                }
                
                content.innerHTML = html;
            }
        }

        function showEdgeDetails(edgeId) {
            const edge = edgesData.find(e => e.id === edgeId);
            if (!edge) return;

            const panel = document.getElementById('details-panel');
            const title = document.getElementById('detail-title');
            const content = document.getElementById('detail-content');

            panel.style.display = 'block';
            title.innerHTML = `Relation: <span style="color: var(--accent);">${edge.label.split('\n')[0]}</span>`;

            const fromNode = nodesData.find(n => n.id === edge.from);
            const toNode = nodesData.find(n => n.id === edge.to);

            let html = `
                <div class="detail-row">
                    <div class="detail-label">From Subject</div>
                    <div class="detail-value">${fromNode ? fromNode.label : ''}</div>
                </div>
                <div class="detail-row">
                    <div class="detail-label">To Value</div>
                    <div class="detail-value">${toNode ? toNode.label : ''}</div>
                </div>
            `;
            
            if (edge.fact) {
                const f = edge.fact;
                const dateStr = new Date(f.timestamp * 1000).toLocaleString();
                html += `
                    <div style="margin-top:10px; font-size:0.75rem; font-weight:600; color: var(--text-muted);">Fact Details:</div>
                    <div style="background: rgba(0,0,0,0.15); padding: 8px; border-radius: 6px; margin-top: 4px; font-size: 0.75rem;">
                        <div><strong>Fact ID:</strong> #${f.id}</div>
                        <div><strong>Source ID:</strong> ${f.source_id}</div>
                        <div><strong>Source Type:</strong> ${f.source_type}</div>
                        ${f.source_url ? `<div><strong>URL:</strong> <a href="${f.source_url}" target="_blank" style="color: var(--accent);">${f.source_url}</a></div>` : ''}
                        <div style="font-size:0.65rem; color: var(--text-muted); margin-top: 2px;">${dateStr}</div>
                    </div>
                `;
            }

            content.innerHTML = html;
        }

        document.getElementById('search-input').addEventListener('input', function(e) {
            const query = e.target.value.toLowerCase().trim();
            if (!query) {
                nodesData.forEach(n => {
                    data.nodes.update({id: n.id, hidden: false});
                });
                return;
            }

            nodesData.forEach(n => {
                const match = n.label.toLowerCase().includes(query) || 
                              (n.subject && n.subject.toLowerCase().includes(query));
                data.nodes.update({
                    id: n.id,
                    hidden: !match
                });
            });
        });

        document.getElementById('btn-zoom-in').addEventListener('click', () => {
            network.moveTo({ scale: network.getScale() * 1.2 });
        });

        document.getElementById('btn-zoom-out').addEventListener('click', () => {
            network.moveTo({ scale: network.getScale() * 0.8 });
        });

        document.getElementById('btn-fit').addEventListener('click', () => {
            network.fit({
                animation: {
                    duration: 500,
                    easingFunction: 'easeInOutQuad'
                }
            });
        });
    </script>
</body>
</html>
"""


def cmd_graphify(output_path: str = "") -> None:
    """Generate a visual knowledge mesh graph representation as an interactive HTML file."""
    if not output_path:
        output_path = str(DATA / "graph.html")

    conn = _db(DATA / "keeper.db")
    if conn is None:
        print("Keeper DB not found. Start agents first.")
        return

    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, subject, predicate, object, source_id, source_url, source_type, timestamp, version FROM facts"
        ).fetchall()
    except Exception as e:
        print(f"Error querying Keeper DB: {e}")
        conn.close()
        return
    conn.close()

    if not rows:
        print("No facts in Keeper DB to visualize.")
        return

    facts = [dict(r) for r in rows]

    # Group by (subject, predicate) to find conflicts
    from collections import defaultdict
    facts_by_sp = defaultdict(list)
    for f in facts:
        facts_by_sp[(f["subject"], f["predicate"])].append(f)

    conflicts = {}
    conflicting_facts = set()
    for (sub, pred), grp in facts_by_sp.items():
        unique_objs = {f["object"] for f in grp}
        if len(unique_objs) > 1:
            conflicts[f"{sub}::{pred}"] = grp
            for f in grp:
                conflicting_facts.add(f["id"])

    # Build nodes & edges
    nodes = []
    edges = []

    added_subjects = set()
    added_objects = {}  # key: (subject, predicate, object_value) -> node_id
    nodes_map = {}      # key: node_id -> list_index

    for f in facts:
        sub = f["subject"]
        if sub not in added_subjects:
            has_conflict = any(key.startswith(f"{sub}::") for key in conflicts)
            if has_conflict:
                color = {
                    "background": "#1e293b",
                    "border": "#f59e0b",
                    "highlight": {"background": "#334155", "border": "#fbbf24"}
                }
                shadow = {"enabled": True, "color": "rgba(245, 158, 11, 0.2)", "size": 10}
            else:
                color = {
                    "background": "#1e3a8a",
                    "border": "#3b82f6",
                    "highlight": {"background": "#2563eb", "border": "#60a5fa"}
                }
                shadow = {"enabled": True, "color": "rgba(59, 130, 246, 0.2)", "size": 8}

            node_id = f"s:{sub}"
            nodes_map[node_id] = len(nodes)
            nodes.append({
                "id": node_id,
                "label": sub,
                "nodeType": "subject",
                "color": color,
                "shape": "dot",
                "size": 25,
                "shadow": shadow
            })
            added_subjects.add(sub)

    for f in facts:
        sub = f["subject"]
        pred = f["predicate"]
        obj = f["object"]
        fid = f["id"]

        obj_key = (sub, pred, obj)
        if obj_key not in added_objects:
            obj_node_id = f"o:{sub}:{pred}:{obj}"
            is_conflict = fid in conflicting_facts

            if is_conflict:
                color = {
                    "background": "#7f1d1d",
                    "border": "#ef4444",
                    "highlight": {"background": "#991b1b", "border": "#f87171"}
                }
                shadow = {"enabled": True, "color": "rgba(239, 68, 68, 0.5)", "size": 12}
                group = "conflict_object"
                size = 22
            else:
                color = {
                    "background": "#064e3b",
                    "border": "#10b981",
                    "highlight": {"background": "#047857", "border": "#34d399"}
                }
                shadow = {"enabled": True, "color": "rgba(16, 185, 129, 0.2)", "size": 8}
                group = "object"
                size = 18

            nodes_map[obj_node_id] = len(nodes)
            nodes.append({
                "id": obj_node_id,
                "label": obj,
                "nodeType": "object",
                "subject": sub,
                "predicate": pred,
                "group": group,
                "color": color,
                "shape": "dot",
                "size": size,
                "shadow": shadow,
                "facts": []
            })
            added_objects[obj_key] = obj_node_id

        node_idx = nodes_map[added_objects[obj_key]]
        nodes[node_idx]["facts"].append(f)

    # Add edges (grouped by subject, predicate, object)
    facts_by_spo = defaultdict(list)
    for f in facts:
        facts_by_spo[(f["subject"], f["predicate"], f["object"])].append(f)

    for (sub, pred, obj), grp in facts_by_spo.items():
        obj_node_id = added_objects[(sub, pred, obj)]
        is_conflict = any(f["id"] in conflicting_facts for f in grp)
        sources = ", ".join({f["source_id"] for f in grp})
        label = f"{pred}\n({sources})"

        if is_conflict:
            edge_color = {"color": "#ef4444", "highlight": "#f87171"}
            width = 3
            dashes = True
        else:
            edge_color = {"color": "rgba(156, 163, 175, 0.5)", "highlight": "#3b82f6"}
            width = 1.5
            dashes = False

        edges.append({
            "id": f"e:{sub}:{pred}:{obj}",
            "from": f"s:{sub}",
            "to": obj_node_id,
            "label": label,
            "color": edge_color,
            "width": width,
            "dashes": dashes,
            "fact": grp[0]
        })

    # Render template
    html = HTML_TEMPLATE.replace("{{NODES_JSON}}", json.dumps(nodes, indent=2))
    html = html.replace("{{EDGES_JSON}}", json.dumps(edges, indent=2))
    html = html.replace("{{CONFLICTS_JSON}}", json.dumps(conflicts, indent=2))

    # Write output
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    abs_path = out_path.resolve()
    print(f"✅ Knowledge Mesh graph generated at: {abs_path}")

    # Try to open in browser
    try:
        import webbrowser
        webbrowser.open(f"file://{abs_path}")
        print("🌍 Opened graph in your default browser.")
    except Exception:
        pass


def cmd_help() -> None:
    print(__doc__)


def main() -> None:
    if len(sys.argv) < 2:
        cmd_help()
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    cmds = {
        "recall": lambda: cmd_recall(args[0] if args else ""),
        "detect": lambda: cmd_detect(),
        "status": lambda: cmd_status(),
        "send": lambda: cmd_send_via_band(" ".join(args)),
        "graphify": lambda: cmd_graphify(args[0] if args else ""),
        "start": lambda: cmd_start(),
        "help": lambda: cmd_help(),
    }

    f = cmds.get(cmd)
    if not f:
        print(f"Unknown: {cmd}")
        cmd_help()
    else:
        f()


if __name__ == "__main__":
    main()
