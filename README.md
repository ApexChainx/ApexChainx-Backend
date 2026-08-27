# ApexChain Frontend

<div align="center">
  <strong>Enterprise Network Operations Intelligence Platform</strong>
  <br/>
  Real-time outage management, SLA enforcement, automated blockchain payments, and analytics.
  <br/>
  <br/>
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" />
  <img alt="React" src="https://img.shields.io/badge/React-19-61DAFB?logo=react" />
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript" />
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green" />
</div>

<br/>

Frontend application for the ApexChain network operations intelligence system.

This repository is the user-facing layer in the 3-repo system:

- `apexchainx-frontend` → frontend (this repo)
- `apexchainx-backend` → backend and integration layer
- `apexchainx-contracts` → Soroban smart contracts

### System Flow

```
User → Frontend → Backend → Smart Contracts → Backend → Frontend
```

### Important Rule

- The frontend does not talk to contracts directly
- All contract interaction must go through `apexchainx-backend`

---

## Table of Contents

- [Overview](#overview)
- [Current App Surface](#current-app-surface)
- [Backend Integration](#backend-integration)
- [Local Setup](#local-setup)
- [Project Structure](#project-structure)
- [Stabilized Baseline](#stabilized-baseline)
- [Current Limitations](#current-limitations)
- [Contributing Notes](#contributing-notes)
- [Related Repositories](#related-repositories)

---

## Overview

`apexchainx-frontend` is a Next.js frontend for viewing outages, reviewing SLA outcomes, exposing payment and configuration screens, and managing webhooks and bulk data imports.

### Tech Stack

| Technology | Purpose |
|-----------|---------|
| **Next.js 16** | React framework with App Router |
| **React 19** | UI component library |
| **TypeScript 5** | Type-safe development |
| **TanStack React Query** | Server state management and caching |
| **TanStack Table** | Data table with sorting, filtering, pagination |
| **Axios** | HTTP client for API communication |
| **Tailwind CSS 4** | Utility-first CSS framework |
| **Radix UI** | Unstyled, accessible UI primitives |
| **Vitest** | Unit and integration testing |

---

## Current App Surface

Active App Router routes live under `src/app`:

| Route | Description |
|-------|-------------|
| `/` | SLA dashboard with KPIs, trends, and analytics |
| `/outages` | Outages list with advanced filtering and export |
| `/outages/new` | Create new outage with validation |
| `/outages/[id]` | Outage details, timeline, resolution, and editing |
| `/payments` | Payment history and transaction tracking |
| `/config` | SLA configuration management |
| `/setting` | User and application settings |
| `/webhooks` | Webhook configuration and event management |
| `/bulk-import` | Bulk outage import with dry-run and history |
| `/login` | User authentication |
| `/register` | New user registration |

### Architecture & State Management

The shared shell and providers live in:

- `src/app/layout.tsx` — Root layout with providers
- `src/components/Navigation.tsx` — Main navigation shell
- `src/providers/react-query.tsx` — TanStack Query provider
- `src/providers/session.tsx` — Authentication and session management

Feature modules are organized under `src/features/` for domain-specific UI and hooks.

The app is offline-first for outage reads: an IndexedDB layer
(`src/lib/persisted-cache.ts`), the React Query cache, and a service worker
(`public/sw.js`) each cache a slice of the data with different lifetimes. See
[docs/offline-cache.md](./docs/offline-cache.md) for what's cached where, TTLs,
hydration rules, and the current purge/eviction gaps.

---

## Backend Integration

The frontend uses the backend API client in `src/lib/api.ts`.

**Base URL:** `http://localhost:8000/api/v1/`

Local development expects the backend running on port `8000`.

### Service Modules

| Module | Purpose |
|--------|---------|
| `src/services/outages.ts` | Outage CRUD operations |
| `src/services/paymentService.ts` | Payment processing and history |
| `src/services/dashboardService.ts` | Dashboard analytics and KPIs |
| `src/services/exportService.ts` | Data export (CSV, JSON) |
| `src/services/bulkImportService.ts` | Bulk outage import and dry-run |
| `src/services/sla.ts` | SLA status and configuration |
| `src/services/webhookService.ts` | Webhook management |

---

## Local Setup

### Prerequisites

- **Node.js** 20+ (LTS recommended)
- **npm** 9+
- Running backend from `apexchainx-backend`

### Quick Start

```bash
# Clone the repository
git clone https://github.com/ApexChainx/ApexChainx-Frontend.git
cd ApexChainx-Frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

The application will be available at **[http://localhost:3000](http://localhost:3000)**.

### Available Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server with hot reload |
| `npm run build` | Create optimized production build |
| `npm run start` | Start production server |
| `npm run lint` | Run ESLint for code quality |
| `npm run test` | Run Vitest test suite |

---

## Expected Local Setup

For the frontend to function meaningfully, start the backend as well:

1. Run `apexchainx-backend` on port `8000`
2. Ensure the backend API is reachable at `http://localhost:8000`
3. Start this frontend on `http://localhost:3000`

> **Note:** Without the backend, the app shell will load, but API-backed views such as outages, exports, bulk import, payments, and analytics will not have live data.

---

## Project Structure

```
apexchainx-frontend/
├── src/
│   ├── app/                  # Next.js App Router pages and layouts
│   ├── components/           # Shared UI components and dashboard widgets
│   │   ├── dashboard/        # SLA dashboard views and charts
│   │   ├── outages/          # Outage-specific UI components
│   │   ├── payments/         # Payment views and drawers
│   │   ├── bulk-import/      # Bulk import views
│   │   ├── shared/           # Shared error states and utilities
│   │   └── ui/               # Low-level UI primitives (button, table, card, etc.)
│   ├── features/             # Feature-specific modules
│   │   └── outages/          # Outage hooks, components, and helpers
│   ├── hooks/                # Shared React hooks (session, SLA config, focus trap)
│   ├── lib/                  # API client, auth helpers, URL utils, environment config
│   │   ├── config/           # Environment configuration
│   │   └── auth/             # Authentication redirect helpers
│   ├── providers/            # React context providers
│   ├── services/             # Backend-facing service modules
│   └── types/                # Shared TypeScript type definitions
├── docs/                     # Project documentation
├── tests/                    # Test files
├── vitest.config.ts          # Vitest test configuration
├── tailwind.config.js        # Tailwind CSS configuration
├── tsconfig.json             # TypeScript configuration
└── package.json              # Project metadata and dependencies
```

---

## Stabilized Baseline

As of the latest stabilization pass:

- ✅ `npm run build` passes without errors
- ✅ `npm run lint` passes (one non-blocking TanStack Table warning)
- ✅ All local UI primitives restored and functional
- ✅ Stale `import.meta.env` usage removed from active service modules
- ✅ Outage pages aligned with backend response shapes
- ✅ Test suite passing with Vitest

---

## Current Limitations

This repository is actively being developed. Current areas of focus:

- `/payments` — transitioning from placeholder to full-featured payment views
- `/setting` — expanding settings capabilities
- `/` — enhancing the SLA dashboard with richer analytics
- Codebase consolidation: merging older page-style screens into the App Router pattern
- API alignment: some frontend flows depend on backend endpoints still being stabilized in `apexchainx-backend`

---

## Contributing Notes

When making frontend changes:

- **Preserve the system rule:** Frontend calls Backend, never Contracts directly
- **Prefer App Router:** Update the App Router implementation first
- **API alignment:** Keep API shapes synchronized with `apexchainx-backend`
- **Integration boundary:** Treat `src/services/` and `src/lib/api.ts` as the boundary
- **Type safety:** No `as` type assertions on raw API responses; use typed generics

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed contribution guidelines.

---

## Related Repositories

| Repository | Description |
|-----------|-------------|
| [`apexchainx-backend`](https://github.com/ApexChainx/apexchainx-backend) | Backend API and integration layer |
| [`apexchainx-contracts`](https://github.com/ApexChainx/apexchainx-contracts) | Soroban smart contracts for SLA enforcement |

---

## Complete End-to-End Walkthrough

This step-by-step guide will take you from a fresh clone to a fully running system with test data in `/outages`. Every step is reproducible by any operator.

### Prerequisites First
Before you begin, ensure you have these installed:
- Node.js 20+ (LTS recommended)
- npm 9+
- Python 3.10+ (for the backend)
- Cargo and Rust (for smart contract deployment)
- Freighter browser extension (for Stellar wallet interactions)
- Git

---

### Step 1: Clone All Three Repositories
First, create a parent directory and clone all three repositories:

```bash
# Create a working directory
mkdir apexchainx && cd apexchainx

# Clone frontend
git clone https://github.com/ApexChainx/ApexChainx-Frontend.git
# Clone backend
git clone https://github.com/ApexChainx/apexchainx-backend.git
# Clone smart contracts
git clone https://github.com/ApexChainx/apexchainx-contracts.git
```

Your directory structure should look like:
```
apexchainx/
├── ApexChainx-Frontend/
├── apexchainx-backend/
└── apexchainx-contracts/
```

---

### Step 2: Set Up and Configure Freighter Wallet
Freighter is required to interact with the Stellar network.

1. **Install Freighter**: Go to [freighter.app](https://freighter.app/) and install the browser extension
2. **Create a new wallet**: Follow the setup process to create a new Stellar wallet
3. **Save your secret key**: Write down your 12-word recovery phrase and keep it secure
4. **Switch to Testnet**: 
   - Open Freighter extension
   - Click on Settings (⚙️)
   - Under "Network", select "Testnet"
   - Confirm the switch

Your Freighter wallet is now ready for testnet development.

---

### Step 3: Fund Your Stellar Test Account
You need testnet XLM to pay for transaction fees and maintain your account.

1. **Get your public key**: In Freighter, copy your wallet's public key (starts with 'G')
2. **Fund with Friendbot**: Run this command in your terminal, replacing your public key:
   ```bash
   curl "https://friendbot.stellar.org?addr=GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
   ```
3. **Verify balance**: Refresh Freighter - you should now have 10,000 testnet XLM

> **Note**: You can repeat this anytime you need more testnet XLM. Friendbot is a free service for Stellar testnet.

---

### Step 4: Deploy Soroban Smart Contracts
First, deploy the smart contracts to the Stellar testnet.

```bash
# Go to the contracts directory
cd ../apexchainx-contracts

# Install Soroban CLI if you haven't already
cargo install --locked soroban-cli

# Build the SLA calculator contract
cd sla_calculator
cargo build --target wasm32-unknown-unknown --release

# Deploy the contract to testnet
# Replace SXXX... with your Freighter wallet's secret key (starts with 'S')
soroban contract deploy \
  --wasm target/wasm32-unknown-unknown/release/sla_calculator.wasm \
  --network testnet \
  --source-account SXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

Save the contract ID that's returned (it starts with 'C'). You'll need this for your environment variables.

**Initialize the contract**:
```bash
# Replace:
# - CCCC... with your newly deployed contract ID
# - SXXX... with your secret key
# - GXXX... with your public key
# - CBBB... with the USDC testnet token address
soroban contract invoke \
  --id CCCC... \
  --network testnet \
  --source-account SXXX... \
  -- initialize \
  --admin GXXX... \
  --usdc_token CBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB \
  --pool_address GXXX...
```

---

### Step 5: Establish a Trustline for USDC
Before you can receive USDC (the stable coin used for SLA payments), you need to establish a trustline.

**Option A: Using Stellar Laboratory (easiest)**
1. Go to [Stellar Laboratory Transaction Builder](https://laboratory.stellar.org/#txbuilder?network=test)
2. Source Account: Paste your public key and click "Fetch sequence number"
3. Under "Operations", click "Add Operation"
4. Operation Type: Change Trust
5. Asset Code: `USDC`
6. Issuer: `GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN` (testnet USDC issuer)
7. Click "Sign in Freighter" to submit the transaction

**Option B: Using Python SDK**
```python
from stellar_sdk import Server, Keypair, TransactionBuilder, Network, Asset

server = Server("https://horizon-testnet.stellar.org")
source_keypair = Keypair.from_secret("SXXX...")  # Your secret key
source_account = server.load_account(source_keypair.public_key)

transaction = (
    TransactionBuilder(
        source_account=source_account,
        network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
        base_fee=100,
    )
    .append_change_trust_op(
        asset=Asset("USDC", "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN")
    )
    .set_timeout(30)
    .build()
)

transaction.sign(source_keypair)
response = server.submit_transaction(transaction)
print(f"Trustline established! Transaction hash: {response['hash']}")
```

---

### Step 6: Set Up and Start the Backend
Now let's get the backend running.

```bash
# Go to backend directory
cd ../apexchainx-backend

# Create Python virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
# source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy .env example and configure it
cp .env.example .env
```

**Configure your backend .env file**:
Open `.env` and update these values:
```env
# Stellar Configuration
STELLAR_NETWORK=testnet
STELLAR_HORIZON_URL=https://horizon-testnet.stellar.org
STELLAR_SOROBAN_RPC_URL=https://soroban-testnet.stellar.org

# Pool Wallet (your Freighter wallet credentials)
STELLAR_POOL_SECRET_KEY=SXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX  # Your secret key
STELLAR_POOL_PUBLIC_KEY=GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX   # Your public key

# Smart Contract IDs - use the values from Step 4
SLA_CONTRACT_ID=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC      # Your deployed contract ID
USDC_TOKEN_ADDRESS=CBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB   # USDC testnet address
APEX_TOKEN_ADDRESS=CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA     # APEX token address

# Payment Settings
AUTO_PAYMENT_ENABLED=true
MAX_AUTO_PAYMENT_AMOUNT=10000
```

**Start the backend server**:
```bash
# Start the FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The backend will be running at `http://localhost:8000`. Verify it's working by visiting `http://localhost:8000/docs` to see the Swagger UI.

---

### Step 7: Set Up and Start the Frontend
With the backend running, set up the frontend.

```bash
# Go to frontend directory in a new terminal window
cd ApexChainx-Frontend

# Install dependencies
npm install

# Copy environment example
cp .env.example .env.local
```

**Configure your frontend .env.local file**:
Open `.env.local` and update with the same contract addresses from Step 4:
```env
# Backend API base URL
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1

# Stellar Network Configuration
NEXT_PUBLIC_STELLAR_NETWORK=testnet
NEXT_PUBLIC_STELLAR_HORIZON_URL=https://horizon-testnet.stellar.org
NEXT_PUBLIC_STELLAR_SOROBAN_RPC_URL=https://soroban-testnet.stellar.org

# Smart Contract Addresses - same as backend
NEXT_PUBLIC_SLA_CONTRACT_ID=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
NEXT_PUBLIC_USDC_TOKEN_ADDRESS=CBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
NEXT_PUBLIC_APEX_TOKEN_ADDRESS=CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

# Feature Flags
NEXT_PUBLIC_AUTO_PAYMENT_ENABLED=true
```

**Start the frontend development server**:
```bash
npm run dev
```

The frontend will be running at `http://localhost:3000`.

---

### Step 8: Create Test Outage Data
Now let's populate the system with test data so you can see outages in the `/outages` page.

**Option A: Using the API directly (curl)**
```bash
# Create a test outage (run this in a new terminal)
curl -X POST http://localhost:8000/api/v1/outages \
  -H "Content-Type: application/json" \
  -d '{
    "site_name": "Downtown Data Center",
    "severity": "critical",
    "detected_at": "2026-01-16T10:00:00Z",
    "description": "Primary router failure causing complete outage",
    "operator_wallet": "GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
  }'

# Create another outage
curl -X POST http://localhost:8000/api/v1/outages \
  -H "Content-Type: application/json" \
  -d '{
    "site_name": "Uptown Cell Tower",
    "severity": "high",
    "detected_at": "2026-01-16T11:30:00Z",
    "description": "Backup power supply failure",
    "operator_wallet": "GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
  }'

# Create a third outage
curl -X POST http://localhost:8000/api/v1/outages \
  -H "Content-Type: application/json" \
  -d '{
    "site_name": "Suburban Exchange",
    "severity": "medium",
    "detected_at": "2026-01-16T14:15:00Z",
    "description": "Fiber cut during construction",
    "operator_wallet": "GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
  }'
```

**Option B: Using the bulk import feature**
1. In your browser, go to `http://localhost:3000/bulk-import`
2. Upload a CSV or JSON file with outage data (see backend documentation for schema)
3. Run a dry-run first to validate
4. Import the data to add multiple outages at once

**Option C: Create manually through the UI**
1. Go to `http://localhost:3000/outages/new`
2. Fill out the outage form with test data
3. Submit to create the outage

---

### Step 9: Verify Everything is Working
1. **Visit the outages page**: Go to `http://localhost:3000/outages`
2. You should see all the test outages you created listed
3. Click on any outage to view details, timeline, and SLA status
4. Test resolving an outage:
   - Open an outage detail page
   - Click "Resolve Outage"
   - Enter resolution details
   - The system will calculate SLA and execute any applicable payments

### Step 10: Test the Complete SLA Flow
To see the full automation in action:
1. Create a critical severity outage
2. Wait 25 minutes (or manually set the resolved_at time to 25 minutes after detected_at)
3. Mark it as resolved
4. The system will calculate a 10-minute SLA violation
5. A penalty payment of $1,000 will be automatically processed on Stellar
6. You can view the transaction hash on the outage details page
7. Verify the transaction on [Stellar Expert](https://stellar.expert/explorer/testnet)

---

## Troubleshooting Common Issues

**Backend won't start**:
- Ensure virtual environment is activated
- Verify all dependencies are installed
- Check that .env file is properly configured with all required variables

**Frontend can't connect to backend**:
- Verify backend is running on port 8000
- Check CORS settings in backend
- Ensure NEXT_PUBLIC_API_BASE_URL is correct in .env.local

**Stellar transactions fail**:
- Ensure you have sufficient XLM balance
- Verify trustline is established for USDC
- Check that Freighter is switched to Testnet
- Confirm contract addresses are correct in both .env files

**Outages not appearing**:
- Check browser console for API errors
- Verify backend created the outages successfully via /docs endpoint
- Ensure you're logged into the frontend application

---

<div align="center">
  <sub>Built with ❤️ by the ApexChain team</sub>
</div>