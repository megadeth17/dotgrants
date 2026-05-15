"""
DotGrants Backend API
FastAPI server that interfaces with the DotGrants ink! contract on local Substrate node.
"""
import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ── Config ──────────────────────────────────────────────────────────────────
NODE_URL   = os.getenv("NODE_URL", "ws://127.0.0.1:9944")
CONTRACT   = os.getenv("CONTRACT_ADDRESS", "")   # set after deploy
ABI_PATH   = Path(os.getenv("ABI_PATH", "/root/dotgrants/target/ink/dotgrants.json"))
DB_PATH    = Path(os.getenv("DB_PATH", "/root/dotgrants-app/backend/grants.db"))

# ── Database ─────────────────────────────────────────────────────────────────
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

# ── Substrate interface ───────────────────────────────────────────────────────
def get_substrate():
    from substrateinterface import SubstrateInterface
    return SubstrateInterface(url=NODE_URL)

def get_contract_instance(substrate):
    from substrateinterface.contracts import ContractInstance
    if not CONTRACT:
        raise HTTPException(503, "Contract not deployed yet — set CONTRACT_ADDRESS env var")
    with open(ABI_PATH) as f:
        metadata = json.load(f)
    return ContractInstance(
        contract_address=CONTRACT,
        metadata=metadata,
        substrate=substrate,
    )

# ── Models ────────────────────────────────────────────────────────────────────
class GrantMeta(BaseModel):
    grant_id:    int
    title:       str
    description: Optional[str] = ""
    tags:        Optional[str] = ""

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
    """Register the deployed contract address."""
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
        substrate = get_substrate()
        contract  = get_contract_instance(substrate)
        result    = contract.read(substrate, "get_grant_count", args={})
        return {"count": result.contract_result_data.value}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/grants/{grant_id}")
def get_grant(grant_id: int):
    try:
        substrate = get_substrate()
        contract  = get_contract_instance(substrate)
        result    = contract.read(substrate, "get_grant", args={"grant_id": grant_id})
        grant     = result.contract_result_data.value
        if grant is None:
            raise HTTPException(404, "Grant not found")

        # Merge with off-chain metadata
        conn = get_db()
        meta = conn.execute(
            "SELECT * FROM grants_meta WHERE grant_id=?", (grant_id,)
        ).fetchone()
        conn.close()

        return {
            "grant_id":         grant_id,
            "funder":           str(grant["funder"]),
            "amount":           str(grant["amount"]),
            "deadline":         grant["deadline"],
            "status":           str(grant["status"]),
            "approved_builder": str(grant.get("approved_builder", "")) if grant.get("approved_builder") else None,
            "metadata_hash":    grant["metadata_hash"].hex() if isinstance(grant.get("metadata_hash"), bytes) else str(grant.get("metadata_hash", "")),
            "title":            meta["title"] if meta else f"Grant #{grant_id}",
            "description":      meta["description"] if meta else "",
            "tags":             meta["tags"] if meta else "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/grants")
def list_grants():
    """List all grants (chain count + off-chain metadata)."""
    try:
        substrate = get_substrate()
        contract  = get_contract_instance(substrate)
        count_res = contract.read(substrate, "get_grant_count", args={})
        count     = count_res.contract_result_data.value or 0

        grants = []
        for i in range(count):
            try:
                result = contract.read(substrate, "get_grant", args={"grant_id": i})
                g = result.contract_result_data.value
                if g:
                    grants.append({
                        "grant_id": i,
                        "funder":   str(g["funder"]),
                        "amount":   str(g["amount"]),
                        "status":   str(g["status"]),
                        "deadline": g["deadline"],
                    })
            except Exception:
                continue

        # Merge off-chain meta
        conn = get_db()
        for grant in grants:
            meta = conn.execute(
                "SELECT * FROM grants_meta WHERE grant_id=?", (grant["grant_id"],)
            ).fetchone()
            if meta:
                grant["title"]       = meta["title"]
                grant["description"] = meta["description"]
                grant["tags"]        = meta["tags"]
            else:
                grant["title"]       = f"Grant #{grant['grant_id']}"
                grant["description"] = ""
                grant["tags"]        = ""
        conn.close()
        return {"grants": grants, "total": count}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/grants/{grant_id}/applications")
def get_applications(grant_id: int):
    try:
        substrate = get_substrate()
        contract  = get_contract_instance(substrate)
        count_res = contract.read(substrate, "get_application_count", args={"grant_id": grant_id})
        count     = count_res.contract_result_data.value or 0

        apps = []
        for i in range(count):
            res = contract.read(substrate, "get_application", args={"grant_id": grant_id, "idx": i})
            a   = res.contract_result_data.value
            if a:
                apps.append({
                    "index":         i,
                    "applicant":     str(a["applicant"]),
                    "proposal_hash": a["proposal_hash"].hex() if isinstance(a.get("proposal_hash"), bytes) else str(a.get("proposal_hash", "")),
                })
        return {"grant_id": grant_id, "applications": apps, "count": count}
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

# ── Node info ──────────────────────────────────────────────────────────────────
@app.get("/node/info")
def node_info():
    try:
        substrate = get_substrate()
        header = substrate.get_block_header()
        return {
            "connected": True,
            "url": NODE_URL,
            "block_number": header["header"]["number"],
            "block_hash": str(substrate.get_block_hash()),
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
