# DotGrants — Permissionless Micro-Grants on PortalDot

> **PortalDot Mini Hackathon Season 1 Submission**

DotGrants is a fully on-chain, permissionless micro-grants protocol built with [ink!](https://use.ink) smart contracts on PortalDot. No committees. No governance votes. Anyone can create a grant, any builder can apply, and payment happens automatically on-chain when the funder approves.

**Every approved grant becomes verifiable builder reputation. Fund once, build a track record forever.**

### Live Demo: [http://178.104.36.180](http://178.104.36.180)

---

## How It Works

```
1. FUND    → Funder deposits POT as reward → locked in contract
2. APPLY   → Any builder submits proposal hash on-chain (permissionless)
3. APPROVE → Funder picks the best builder → POT auto-transfers
4. TRACK   → Completed grants build on-chain reputation for builders
```

If deadline passes with no approval → funder reclaims POT. Zero trust assumptions.

---

## The Problem

PortalDot has a native `bounties` pallet, but it requires a governance council to approve every payout. This creates friction for individual funders and small teams who want to reward builders directly.

## The Solution

DotGrants removes the middleman. Plus, it adds something governance bounties don't have: **a builder reputation layer** derived from on-chain grant history. Every approved grant = verifiable track record.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Permissionless Grants** | Anyone can fund or apply. No approval gates. |
| **On-chain Escrow** | POT locked in contract until approval or deadline reclaim |
| **Builder Reputation** | Leaderboard computed from on-chain approved grants — verifiable track record |
| **POT Native** | All transactions use POT as gas and settlement token |
| **Metadata Hashing** | Proposal details hashed with SHA-256, stored on-chain for integrity |
| **Auto-transfer** | Approval triggers instant POT transfer to builder |

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│  Frontend       │     │  Backend API     │     │  ink! Contract       │
│  Vanilla HTML/JS│◄───►│  FastAPI +       │◄───►│  PortalDot Node      │
│  Orbitron/Exo 2 │     │  cargo-contract  │     │  WASM (19.1K)        │
│  Polkadot.js    │     │  CLI bridge      │     │  POT as gas          │
└─────────────────┘     └──────────────────┘     └──────────────────────┘
```

| Layer | Tech | Notes |
|-------|------|-------|
| **Contract** | ink! 5.1.1 (Rust) → WASM | 19.1K compiled, 5/5 tests pass |
| **Backend** | Python + FastAPI + `cargo contract call --output-json` | Subprocess bridge — guaranteed ABI compatibility |
| **Frontend** | Vanilla HTML/CSS/JS | Orbitron + Exo 2 typography, dark theme |
| **Storage** | On-chain `Mapping<u64, Grant>` + Off-chain SQLite | Chain = source of truth, SQLite = metadata cache |
| **Node** | swanky-node v1.7.0 (ARM64) | Local dev node with contracts pallet |
| **Infra** | Hetzner VPS (ARM64, 4GB) + Docker (nginx:alpine) | Live at 178.104.36.180 |

### Why `cargo-contract` CLI bridge?

Standard Python Substrate SDKs have SCALE codec version mismatches with newer contract pallets. Using `cargo contract call --output-json` via subprocess guarantees ABI compatibility since it uses the same toolchain that compiled the contract. A recursive JSON unwrapper (`_unwrap`) handles the nested `Ok(Some(Tuple(...)))` structure from cargo-contract output.

---

## Smart Contract

Located in `contract/lib.rs`.

### Storage

```rust
grants:        Mapping<u64, Grant>,
applications:  Mapping<(u64, u64), Application>,  // flat mapping (no Vec<T>)
app_counts:    Mapping<u64, u64>,
next_grant_id: u64,
```

### Messages

| Function | Access | Description |
|----------|--------|-------------|
| `create_grant` | Anyone (payable) | Deposit POT + set metadata hash + deadline |
| `apply_for_grant` | Anyone | Submit proposal hash on-chain |
| `approve_applicant` | Funder only | Approve builder → auto-transfer POT |
| `reclaim` | Funder only | Reclaim POT after deadline (if no approval) |
| `get_grant` | View | Read grant state |
| `get_application` | View | Read specific application |
| `get_application_count` | View | Count applications per grant |
| `get_grant_count` | View | Total grants created |

### Events

- `GrantCreated { grant_id, funder, amount }`
- `ApplicationSubmitted { grant_id, applicant }`
- `GrantApproved { grant_id, builder, amount }`
- `GrantReclaimed { grant_id, funder, amount }`

### Tests (5/5 pass)

```
create_grant_works
apply_works
zero_amount_fails
only_funder_can_approve
non_applicant_cannot_be_approved
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check + contract address |
| GET | `/node/info` | Block number + connection status |
| GET | `/grants` | List all grants with metadata |
| GET | `/grants/{id}` | Grant detail + on-chain state |
| GET | `/grants/{id}/applications` | Applications for a grant |
| GET | `/builders/leaderboard` | Builder reputation from chain data |
| POST | `/grants/{id}/meta` | Save off-chain metadata (title, desc, tags) |
| POST | `/deploy` | Register new contract address |

---

## Running Locally

### Prerequisites

- Rust + `cargo-contract` 5.0.3+
- Python 3.12+ with `fastapi`, `uvicorn`, `pydantic`
- swanky-node or substrate-contracts-node
- Docker (optional, for frontend)

### 1. Build & Test Contract

```bash
cd contract
cargo contract build --release
cargo test
```

### 2. Start Node

```bash
./swanky-node --dev --rpc-external --rpc-cors=all
```

### 3. Deploy Contract

```bash
cargo contract instantiate target/ink/dotgrants.contract \
  --suri //Alice --execute --url ws://127.0.0.1:9944
```

### 4. Start Backend

```bash
cd backend
export CONTRACT_ADDRESS=<deployed-address>
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 5. Serve Frontend

```bash
cd frontend
docker run -d -p 80:80 -v $(pwd):/usr/share/nginx/html:ro nginx:alpine
```

Or with Docker Compose:

```bash
CONTRACT_ADDRESS=<addr> docker-compose up -d
```

---

## POT Usage

DotGrants uses **POT as gas** for all on-chain transactions (contract deployment, grant creation, applications, approvals, reclaims) and as the **settlement currency** locked in grant escrow. This is a PortalDot-native application — POT is central to every interaction.

---

## Why DotGrants?

- **For builders**: permissionless funding + on-chain reputation that compounds over time
- **For funders**: direct, trustless payment without governance overhead
- **For PortalDot**: missing layer between governance bounties and direct grants
- **For judges**: real ink! contract patterns (Mapping, payable, events, error handling), real-world market potential (grants infrastructure), live deployed MVP

---

## License

MIT — open source, free to fork and deploy.

---

*Built for [PortalDot Mini Hackathon Season 1](https://portaldot.network) — May 2026*
