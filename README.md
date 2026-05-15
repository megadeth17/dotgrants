# DotGrants — Permissionless Micro-Grants on Portaldot

> **Portaldot Mini Hackathon Season 1 Submission**

DotGrants is a fully on-chain, permissionless micro-grants protocol built with [Ink!](https://use.ink) smart contracts on Portaldot. No committees. No governance votes. Anyone can create a grant, any builder can apply, and payment happens automatically on-chain when the funder approves.

---

## The Problem

Portaldot has a native `bounties` pallet — but it requires a governance council to approve every payout. This creates friction for individual funders and small teams who just want to reward builders directly.

## The Solution

DotGrants removes the middleman:

1. **Funder** creates a grant → deposits POT as reward → sets deadline
2. **Builder** applies → submits proposal hash on-chain
3. **Funder** reviews applicants → approves one → POT transfers automatically
4. If deadline passes with no approval → funder reclaims POT

100% permissionless. Every action is trustless and transparent on-chain.

---

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│  Frontend       │    │  Backend API     │    │  Ink! Contract      │
│  HTML/JS        │◄──►│  FastAPI         │◄──►│  Portaldot Node     │
│  Polkadot.js    │    │  substrateinterface│  │  WASM / POT token   │
└─────────────────┘    └──────────────────┘    └─────────────────────┘
```

| Layer       | Tech                                         |
|-------------|----------------------------------------------|
| Contract    | Ink! 5.1.1 (Rust) → WASM                    |
| Backend     | Python 3.12 + FastAPI + substrateinterface   |
| Frontend    | Vanilla HTML/CSS/JS + Polkadot.js extension  |
| Storage     | On-chain (Mapping) + Off-chain (SQLite)      |
| Node        | substrate-contracts-node (local)             |

---

## Smart Contract

Located in `contract/lib.rs`. Key storage:

```rust
grants:       Mapping<u64, Grant>,
applications: Mapping<(u64, u64), Application>,  // flat mapping, no Vec<T> issues
app_counts:   Mapping<u64, u64>,
next_grant_id: u64,
```

### Messages

| Function              | Access   | Description                              |
|-----------------------|----------|------------------------------------------|
| `create_grant`        | Anyone   | Payable — deposit POT + set metadata     |
| `apply_for_grant`     | Anyone   | Submit proposal hash on-chain            |
| `approve_applicant`   | Funder   | Approve builder → auto-transfer POT      |
| `reclaim`             | Funder   | Reclaim POT after deadline (if no approval) |
| `get_grant`           | View     | Read grant state                         |
| `get_application`     | View     | Read a specific application              |
| `get_application_count`| View   | Count of applications per grant          |
| `get_grant_count`     | View     | Total grants created                     |

### Events

- `GrantCreated { grant_id, funder, amount }`
- `ApplicationSubmitted { grant_id, applicant }`
- `GrantApproved { grant_id, builder, amount }`
- `GrantReclaimed { grant_id, funder, amount }`

---

## Running Locally

### Prerequisites

- Rust + `cargo-contract` 5.0.3+
- Python 3.12+
- `substrate-contracts-node` (local dev node)
- Docker (optional)

### 1. Build the Contract

```bash
cd contract
rustup target add wasm32-unknown-unknown
rustup component add rust-src
cargo install cargo-contract --version 5.0.3
cargo contract build --release
```

Output: `target/ink/dotgrants.contract` (deploy this)

### 2. Run Tests

```bash
cargo test
# 5 tests should pass:
# create_grant_works
# apply_works
# zero_amount_fails
# only_funder_can_approve
# non_applicant_cannot_be_approved
```

### 3. Start Local Node

```bash
# Download from https://github.com/paritytech/substrate-contracts-node/releases
substrate-contracts-node --dev --rpc-external --rpc-cors=all
```

### 4. Deploy Contract

```bash
cargo contract instantiate target/ink/dotgrants.contract \
  --suri //Alice \
  --execute \
  --url ws://127.0.0.1:9944
# Note the deployed contract address
```

Or use [Contracts UI](https://contracts-ui.substrate.io) for a graphical interface.

### 5. Start Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export NODE_URL=ws://127.0.0.1:9944
export CONTRACT_ADDRESS=<your-deployed-address>
export ABI_PATH=../contract/target/ink/dotgrants.json

uvicorn main:app --host 0.0.0.0 --port 8000
```

API docs: `http://localhost:8000/docs`

### 6. Open Frontend

Open `frontend/index.html` in a browser, or serve it:

```bash
cd frontend
python3 -m http.server 80
```

### Docker (all-in-one)

```bash
CONTRACT_ADDRESS=<addr> docker-compose up -d
```

---

## API Endpoints

| Method | Path                          | Description                    |
|--------|-------------------------------|--------------------------------|
| GET    | `/health`                     | Health check                   |
| GET    | `/node/info`                  | Current block + connection     |
| GET    | `/grants`                     | List all grants                |
| GET    | `/grants/{id}`                | Get grant + metadata           |
| GET    | `/grants/{id}/applications`   | List applications for a grant  |
| POST   | `/grants/{id}/meta`           | Save off-chain metadata        |
| POST   | `/deploy`                     | Register contract address      |

---

## Why DotGrants?

- **For the ecosystem**: builders need funding, funders need a simple tool — this is the missing layer
- **For judges**: demonstrates real Ink! contract patterns (Mapping, payable, events, error types)
- **Narrative**: Portaldot has governance bounties; DotGrants adds the permissionless layer for individuals

---

## License

MIT — open source, free to fork and deploy.

---

*Built for [Portaldot Mini Hackathon Season 1](https://portaldot.network) — May 2026*
