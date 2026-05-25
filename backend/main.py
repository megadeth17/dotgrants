"""
DotGrants Backend API
FastAPI server that interfaces with the DotGrants ink! contract via cargo-contract CLI.
"""
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ── Config ───────────────────────────────────────────────────────────────────
NODE_URL      = os.getenv("NODE_URL", "ws://127.0.0.1:9944")
CONTRACT      = os.getenv("CONTRACT_ADDRESS", "5EdB8JZZYC3JyCeivXkHU24kGVWvypCsCZA4vMmCmcmq1Jsk")
CONTRACT_DIR  = os.getenv("CONTRACT_DIR",  "/root/dotgrants")
DB_PATH       = Path(os.getenv("DB_PATH", "/root/dotgrants-app/backend/grants.db"))
CARGO         = os.getenv("CARGO_BIN", "/root/.cargo/bin/cargo")
SURI          = "//Alice"   # read-only calls — any funded account works

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS grants_meta (
            grant_id    INTEGER PRIMARY KEY,
            title       TEXT NOT NULL,
            description TEXT,
            tags        TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_addr TEXT NOT NULL,
            deployed_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

# ── cargo contract CLI bridge ─────────────────────────────────────────────────
def cargo_call(message: str, args: list[str] | None = None) -> dict:
    """
    Run `cargo contract call --output-json` and return the parsed JSON data.
    Raises HTTPException on failure.
    """
    if not CONTRACT:
        raise HTTPException(503, "Contract address not set. POST /deploy first.")

    cmd = [
        CARGO, "contract", "call",
        "--contract", CONTRACT,
        "--message",  message,
        "--suri",     SURI,
        "--url",      NODE_URL,
        "--output-json",
    ]
    if args:
        cmd += ["--args"] + args

    env = {**os.environ, "PATH": f"/root/.cargo/bin:{os.environ.get('PATH', '')}"}
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=CONTRACT_DIR, env=env)

    # cargo-contract writes warnings to stderr, JSON to stdout
    raw = proc.stdout.strip()
    if not raw:
        raise HTTPException(500, f"cargo contract call returned no output: {proc.stderr[:400]}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # strip leading warning lines and try again
        lines = [l for l in proc.stdout.splitlines() if l.strip().startswith("{")]
        if lines:
            return json.loads(lines[0])
        raise HTTPException(500, f"Cannot parse JSON: {proc.stdout[:400]}")


def cargo_execute(message: str, args: list[str] | None = None,
                  suri: str = "//Alice", value: str | None = None) -> dict:
    """
    Run `cargo contract call --execute --output-json` for state-changing calls.
    Returns parsed JSON or raises HTTPException.
    """
    if not CONTRACT:
        raise HTTPException(503, "Contract address not set.")

    cmd = [
        CARGO, "contract", "call",
        "--contract", CONTRACT,
        "--message",  message,
        "--suri",     suri,
        "--url",      NODE_URL,
        "--output-json",
        "--execute",
        "--skip-confirm",
    ]
    if args:
        cmd += ["--args"] + args
    if value:
        cmd += ["--value", value]

    env = {**os.environ, "PATH": f"/root/.cargo/bin:{os.environ.get('PATH', '')}"}
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=CONTRACT_DIR, env=env,
                          timeout=60)

    raw = proc.stdout.strip()
    if proc.returncode != 0 and not raw:
        raise HTTPException(500, f"Transaction failed: {proc.stderr[:500]}")

    try:
        parsed = json.loads(raw)
        # cargo contract call --execute returns a list of events
        if isinstance(parsed, list):
            return {"status": "ok", "events": parsed}
        return parsed
    except json.JSONDecodeError:
        lines = [l for l in proc.stdout.splitlines() if l.strip().startswith("{")]
        if lines:
            return json.loads(lines[-1])
        # If no JSON but returncode 0, tx might have succeeded
        if proc.returncode == 0:
            return {"status": "ok", "output": proc.stdout[:300]}
        raise HTTPException(500, f"Cannot parse tx result: {proc.stdout[:400]} | stderr: {proc.stderr[:200]}")

# ── JSON → Python type extractors ─────────────────────────────────────────────
def _unwrap(node: dict):
    """Recursively unwrap cargo-contract JSON value nodes into Python scalars."""
    if node is None:
        return None
    if "Literal" in node:
        return node["Literal"]
    if "UInt" in node:
        return node["UInt"]
    if "Bool" in node:
        return node["Bool"]
    if "Tuple" in node:
        t = node["Tuple"]
        ident  = t.get("ident", "")
        values = t.get("values", [])
        if ident == "Ok":
            return _unwrap(values[0]) if values else None
        if ident == "Some":
            return _unwrap(values[0]) if values else None
        if ident == "None":
            return None
        # enum variant with no fields (e.g. Open, Approved, Reclaimed)
        if not values:
            return ident
        return {ident: [_unwrap(v) for v in values]}
    if "Map" in node:
        return {k: _unwrap(v) for k, v in node["Map"].items()}
    if "Seq" in node:
        return [_unwrap(e) for e in node["Seq"].get("elems", [])]
    return node

def _extract(result: dict):
    """Extract the Ok(..) value from a cargo-contract JSON result."""
    return _unwrap(result.get("data", {}))

def _hex_bytes(seq) -> str:
    """Convert a list of ints (byte array from contract) to hex string."""
    if isinstance(seq, list):
        return bytes(seq).hex()
    return str(seq)

# ── Parsers ───────────────────────────────────────────────────────────────────
def parse_grant(raw: dict) -> dict | None:
    """raw = cargo_call result dict; returns normalized grant or None."""
    value = _extract(raw)   # Ok(Some(Grant{...})) → dict or None
    if value is None:
        return None
    approved = value.get("approved_builder")
    return {
        "funder":           value.get("funder", ""),
        "amount":           str(value.get("amount", 0)),
        "metadata_hash":    _hex_bytes(value.get("metadata_hash", [])),
        "deadline":         value.get("deadline", 0),
        "status":           value.get("status", ""),
        "approved_builder": approved if isinstance(approved, str) else None,
    }

def parse_application(raw: dict) -> dict | None:
    value = _extract(raw)
    if value is None:
        return None
    return {
        "applicant":     value.get("applicant", ""),
        "proposal_hash": _hex_bytes(value.get("proposal_hash", [])),
    }

def parse_u64(raw: dict) -> int:
    return int(_extract(raw) or 0)

# ── Models ────────────────────────────────────────────────────────────────────
class GrantMeta(BaseModel):
    grant_id:    int
    title:       str
    description: Optional[str] = ""
    tags:        Optional[str] = ""


class CreateGrantRequest(BaseModel):
    title: str
    description: Optional[str] = ""
    tags: Optional[str] = ""
    amount: int  # in planck
    deadline: int  # unix timestamp ms
    suri: Optional[str] = "//Alice"

class ApplyRequest(BaseModel):
    proposal: str
    suri: Optional[str] = "//Alice"

class ApproveRequest(BaseModel):
    suri: Optional[str] = "//Alice"

class DeployRequest(BaseModel):
    contract_address: str

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="DotGrants API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "node": NODE_URL, "contract": CONTRACT or "not-set"}

# ── Deployment ────────────────────────────────────────────────────────────────
@app.post("/deploy")
def set_deployment(req: DeployRequest):
    global CONTRACT
    CONTRACT = req.contract_address
    conn = get_db()
    conn.execute("INSERT INTO deployments(contract_addr) VALUES(?)", (CONTRACT,))
    conn.commit()
    conn.close()
    return {"contract_address": CONTRACT}

# ── Chain reads ───────────────────────────────────────────────────────────────
@app.get("/grants/count")
def grant_count():
    try:
        count = parse_u64(cargo_call("get_grant_count"))
        return {"count": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/grants/{grant_id}")
def get_grant(grant_id: int):
    try:
        grant = parse_grant(cargo_call("get_grant", [str(grant_id)]))
        if grant is None:
            raise HTTPException(404, "Grant not found")

        conn = get_db()
        meta = conn.execute(
            "SELECT * FROM grants_meta WHERE grant_id=?", (grant_id,)
        ).fetchone()
        conn.close()

        return {
            "grant_id":         grant_id,
            "funder":           grant["funder"],
            "amount":           grant["amount"],
            "deadline":         grant["deadline"],
            "status":           grant["status"],
            "approved_builder": grant["approved_builder"],
            "metadata_hash":    grant["metadata_hash"],
            "title":            meta["title"]       if meta else f"Grant #{grant_id}",
            "description":      meta["description"] if meta else "",
            "tags":             meta["tags"]        if meta else "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/grants")
def list_grants():
    try:
        count = parse_u64(cargo_call("get_grant_count"))
        conn  = get_db()
        grants = []
        for i in range(count):
            try:
                grant = parse_grant(cargo_call("get_grant", [str(i)]))
                if grant is None:
                    continue
                meta = conn.execute(
                    "SELECT * FROM grants_meta WHERE grant_id=?", (i,)
                ).fetchone()
                grants.append({
                    "grant_id":    i,
                    "funder":      grant["funder"],
                    "amount":      grant["amount"],
                    "status":      grant["status"],
                    "deadline":    grant["deadline"],
                    "title":       meta["title"]       if meta else f"Grant #{i}",
                    "description": meta["description"] if meta else "",
                    "tags":        meta["tags"]        if meta else "",
                })
            except Exception:
                continue
        conn.close()
        return {"grants": grants, "total": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/grants/{grant_id}/applications")
def get_applications(grant_id: int):
    try:
        count = parse_u64(cargo_call("get_application_count", [str(grant_id)]))
        apps  = []
        for i in range(count):
            try:
                app_data = parse_application(
                    cargo_call("get_application", [str(grant_id), str(i)])
                )
                if app_data:
                    apps.append({"index": i, **app_data})
            except Exception:
                continue
        return {"grant_id": grant_id, "applications": apps, "count": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Off-chain metadata ────────────────────────────────────────────────────────
@app.post("/grants/{grant_id}/meta")
def save_meta(grant_id: int, meta: GrantMeta):
    conn = get_db()
    conn.execute("""
        INSERT INTO grants_meta(grant_id, title, description, tags)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(grant_id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            tags=excluded.tags
    """, (grant_id, meta.title, meta.description, meta.tags))
    conn.commit()
    conn.close()
    return {"saved": True, "grant_id": grant_id}



# ── Execute endpoints (demo mode — uses dev account) ─────────────────────────
import hashlib

@app.post("/grants/create")
def create_grant_tx(req: CreateGrantRequest):
    """Create a grant on-chain. Demo mode: signs with //Alice or provided suri."""
    try:
        # Hash metadata
        meta_str = json.dumps({"title": req.title, "description": req.description, "tags": req.tags})
        meta_hash = hashlib.sha256(meta_str.encode()).digest()
        # Convert to list of ints for ink! [u8; 32]
        hash_arr = "[" + ",".join(str(b) for b in meta_hash) + "]"

        result = cargo_execute(
            "create_grant",
            args=[hash_arr, str(req.deadline)],
            suri=req.suri,
            value=str(req.amount),
        )

        # Try to extract grant_id from events
        grant_id = None
        events = result.get("events", [])
        for ev in events:
            if "GrantCreated" in str(ev):
                # Try to find grant_id in event data
                if isinstance(ev, dict):
                    data = ev.get("data", {})
                    if isinstance(data, dict) and "grant_id" in data:
                        grant_id = data["grant_id"]

        # If can't extract from events, get count - 1
        if grant_id is None:
            try:
                count = parse_u64(cargo_call("get_grant_count"))
                grant_id = count - 1
            except Exception:
                grant_id = -1

        # Save off-chain metadata
        if grant_id >= 0:
            conn = get_db()
            conn.execute("""
                INSERT INTO grants_meta(grant_id, title, description, tags)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(grant_id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    tags=excluded.tags
            """, (grant_id, req.title, req.description, req.tags))
            conn.commit()
            conn.close()

        return {"success": True, "grant_id": grant_id, "tx": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/grants/{grant_id}/apply")
def apply_for_grant_tx(grant_id: int, req: ApplyRequest):
    """Apply for a grant on-chain. Hashes proposal and submits."""
    try:
        proposal_hash = hashlib.sha256(req.proposal.encode()).digest()
        hash_arr = "[" + ",".join(str(b) for b in proposal_hash) + "]"

        result = cargo_execute(
            "apply_for_grant",
            args=[str(grant_id), hash_arr],
            suri=req.suri,
        )
        return {"success": True, "grant_id": grant_id, "tx": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/grants/{grant_id}/approve/{builder}")
def approve_applicant_tx(grant_id: int, builder: str, req: ApproveRequest):
    """Approve a builder and auto-transfer POT."""
    try:
        result = cargo_execute(
            "approve_applicant",
            args=[str(grant_id), builder],
            suri=req.suri,
        )
        return {"success": True, "grant_id": grant_id, "builder": builder, "tx": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/grants/{grant_id}/reclaim")
def reclaim_grant_tx(grant_id: int, req: ApproveRequest):
    """Reclaim POT after deadline."""
    try:
        result = cargo_execute(
            "reclaim",
            args=[str(grant_id)],
            suri=req.suri,
        )
        return {"success": True, "grant_id": grant_id, "tx": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Builder Reputation ───────────────────────────────────────────────────────
@app.get("/builders/leaderboard")
def builder_leaderboard():
    """Compute builder reputation from on-chain grant data."""
    try:
        count = parse_u64(cargo_call("get_grant_count"))
        builders = {}
        for i in range(count):
            try:
                grant = parse_grant(cargo_call("get_grant", [str(i)]))
                if grant is None:
                    continue
                status = (grant.get("status") or "").replace("GrantStatus::", "")
                builder = grant.get("approved_builder")
                if status == "Approved" and builder:
                    if builder not in builders:
                        builders[builder] = {"address": builder, "grants_completed": 0, "total_earned": 0}
                    builders[builder]["grants_completed"] += 1
                    builders[builder]["total_earned"] += int(grant.get("amount", 0))
            except Exception:
                continue

        leaderboard = sorted(builders.values(), key=lambda b: b["total_earned"], reverse=True)
        # Add rank
        for idx, b in enumerate(leaderboard):
            b["rank"] = idx + 1
            b["total_earned_pot"] = str(b["total_earned"] // 10**12)
            b["total_earned"] = str(b["total_earned"])

        return {"builders": leaderboard, "total_builders": len(leaderboard)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Node info (uses substrate-interface, OK for basic RPC) ────────────────────
@app.get("/node/info")
def node_info():
    try:
        from substrateinterface import SubstrateInterface
        si     = SubstrateInterface(url=NODE_URL, ss58_format=42)
        header = si.get_block_header()
        return {
            "connected":    True,
            "url":          NODE_URL,
            "block_number": header["header"]["number"],
            "block_hash":   str(si.get_block_hash()),
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
