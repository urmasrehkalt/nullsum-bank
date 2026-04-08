# NullSum Bank

> *Topeltmäng: `null` programmeerimisest + "zero-sum game" finantsist.*

Branch bank API for the TAK25 school project — integrates with a Central Bank to enable user accounts, transfers, and inter-bank payments.

---

## Technologies

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| API Framework | FastAPI 0.115 |
| Database | SQLite (via SQLAlchemy async + aiosqlite) |
| Auth | JWT — HS256 (user tokens), ES256 (inter-bank) |
| HTTP Client | httpx (async) |
| Server | uvicorn |

---

## Architecture

Modular monolith — one process with clearly separated service modules:

```
app/
├── main.py                      # FastAPI app + startup + background tasks
├── config.py                    # Settings (pydantic-settings, reads .env)
├── database.py                  # Async SQLAlchemy engine + session factory
├── models.py                    # SQLAlchemy ORM models
├── schemas.py                   # Pydantic request/response schemas
├── auth.py                      # JWT user tokens + ES256 key management
├── services/
│   ├── user_service.py          # User CRUD
│   ├── account_service.py       # Account creation + balance management
│   ├── transfer_service.py      # Transfer logic, routing, retry, timeout
│   └── central_bank_service.py  # Registration, heartbeat, directory, rates
└── routers/
    ├── users.py                 # POST /users
    ├── accounts.py              # POST /users/{id}/accounts, GET /accounts/{num}
    └── transfers.py             # POST /transfers, POST /transfers/receive, GET /transfers/{id}
```

### Background tasks (started at boot)
- **Heartbeat loop** — sends heartbeat to Central Bank every 25 minutes
- **Retry loop** — retries pending inter-bank transfers every minute with exponential backoff

---

## Database Schema

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,          -- "user-<uuid4>"
    full_name TEXT NOT NULL,
    email TEXT,
    api_key TEXT NOT NULL UNIQUE, -- Bearer JWT token
    created_at TEXT NOT NULL
);

CREATE TABLE accounts (
    account_number TEXT PRIMARY KEY,  -- 8 chars: BANK_PREFIX[3] + RANDOM[5]
    owner_id TEXT NOT NULL,           -- FK → users.id
    currency TEXT NOT NULL,           -- ISO 4217 (EUR, USD, GBP ...)
    balance TEXT NOT NULL,            -- decimal string, e.g. "100.50"
    created_at TEXT NOT NULL
);

CREATE TABLE transfers (
    transfer_id TEXT PRIMARY KEY,     -- UUID (idempotency key)
    source_account TEXT NOT NULL,
    destination_account TEXT NOT NULL,
    amount TEXT NOT NULL,
    converted_amount TEXT,            -- set when currency conversion occurred
    exchange_rate TEXT,               -- 6 decimal places
    rate_captured_at TEXT,
    status TEXT NOT NULL,             -- completed | failed | pending | failed_timeout
    timestamp TEXT NOT NULL,
    pending_since TEXT,               -- set when transfer goes pending
    next_retry_at TEXT,               -- exponential backoff target time
    retry_count INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE TABLE bank_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
    -- stores: bank_id, bank_prefix, registered_at, last_heartbeat_at,
    --         banks_cache (JSON), banks_cache_at,
    --         exchange_rates_cache (JSON), rates_cache_at
);
```

---

## Installation & Running

### 1. Clone and set up environment

```bash
git clone https://github.com/urmasrehkalt/nullsum-bank.git
cd nullsum-bank

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:
```env
BANK_NAME=NullSum Bank
BANK_ADDRESS=https://your-domain.com    # must be HTTPS for central bank registration
CENTRAL_BANK_URL=https://test.diarainfra.com/central-bank
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
```

### 3. Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

On startup the app will:
1. Create database tables
2. Generate EC P-256 key pair in `keys/` (if not present)
3. Register with the Central Bank
4. Start heartbeat + retry background tasks

API docs available at `http://localhost:8000/docs`

---

## VPS Deployment (systemd + Nginx)

### systemd service `/etc/systemd/system/nullsum-bank.service`

```ini
[Unit]
Description=NullSum Bank API
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/nullsum-bank
EnvironmentFile=/opt/nullsum-bank/.env
ExecStart=/opt/nullsum-bank/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable nullsum-bank
sudo systemctl start nullsum-bank
```

### Nginx `/etc/nginx/sites-available/nullsum-bank`

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Example Requests

### Register a user
```bash
curl -s -X POST https://your-domain.com/api/v1/users \
  -H "Content-Type: application/json" \
  -d '{"fullName": "Alice Smith", "email": "alice@example.com"}' | jq .
```

Response:
```json
{
  "userId": "user-550e8400-e29b-41d4-a716-446655440000",
  "fullName": "Alice Smith",
  "email": "alice@example.com",
  "createdAt": "2026-04-08T10:00:00+00:00",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### Create an account
```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
USER_ID="user-550e8400-e29b-41d4-a716-446655440000"

curl -s -X POST https://your-domain.com/api/v1/users/$USER_ID/accounts \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"currency": "EUR"}' | jq .
```

Response:
```json
{
  "accountNumber": "EST1A2B3",
  "ownerId": "user-550e8400-e29b-41d4-a716-446655440000",
  "currency": "EUR",
  "balance": "0.00",
  "createdAt": "2026-04-08T10:01:00+00:00"
}
```

### Look up an account (public, no auth)
```bash
curl -s https://your-domain.com/api/v1/accounts/EST1A2B3 | jq .
```

### Initiate a transfer
```bash
curl -s -X POST https://your-domain.com/api/v1/transfers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "transferId": "550e8400-e29b-41d4-a716-446655440001",
    "sourceAccount": "EST1A2B3",
    "destinationAccount": "EST9X8Y7",
    "amount": "25.00"
  }' | jq .
```

### Check transfer status
```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  https://your-domain.com/api/v1/transfers/550e8400-e29b-41d4-a716-446655440001 | jq .
```

---

## Transfer Flow

```
POST /transfers
     │
     ├─ Idempotency check (transferId already exists?)
     ├─ Validate source account ownership
     ├─ Check balance
     │
     ├─ destination prefix == own prefix?
     │   YES → Internal transfer (debit + credit, single DB transaction)
     │           status: completed
     │
     └─ NO  → External transfer
               ├─ Debit source account
               ├─ Sign JWT (ES256) with bank private key
               ├─ POST to destination bank /transfers/receive
               │   ├─ 200 → status: completed
               │   ├─ 5xx → status: pending (retry with exponential backoff)
               │   └─ 4xx → status: failed (refund source)
               └─ Retry loop: 1m → 2m → 4m → ... → 60m cap
                  After 4h total → status: failed_timeout + refund
```

---

## Live URL

`https://your-domain.com` *(to be updated after VPS deployment)*
