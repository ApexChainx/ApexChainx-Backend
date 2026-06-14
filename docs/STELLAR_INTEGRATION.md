# Stellar Integration Guide

This guide explains how ApexChainx integrates with the Stellar blockchain network to enable automated SLA-based payments, instant settlements, and immutable audit trails.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Wallet Management](#wallet-management)
- [SLA Payment System](#sla-payment-system)
- [Smart Contracts](#smart-contracts)
- [Testing on Testnet](#testing-on-testnet)
- [Deployment to Mainnet](#deployment-to-mainnet)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Overview

### What is Stellar?

[Stellar](https://stellar.org/) is a fast, low-cost blockchain network designed for payments and asset transfers. Transactions confirm in 3-5 seconds with fees of ~$0.00001.

### Why Stellar for ApexChainx?

ApexChainx integrates Stellar to solve key problems in telecom network operations:

1. **Slow Payments**: Traditional bank transfers take days. Stellar settles in seconds.
2. **Manual SLA Tracking**: Smart contracts automatically calculate penalties/rewards.
3. **Trust Issues**: Blockchain provides transparent, immutable records.
4. **Cross-Border Payments**: Stellar enables instant international settlements.
5. **High Transaction Costs**: Stellar fees are negligible compared to wire transfers.

### Key Features

- **Automated SLA Payments**: Smart contracts trigger penalty or reward payments based on MTTR
- **Instant Settlements**: Payments confirm in 3-5 seconds
- **Multi-Currency**: Support for USDC (payments), ApexChainx tokens (rewards), XLM (fees)
- **Immutable Audit Trails**: RCA reports hashed and stored on-chain
- **Transparent**: All transactions viewable on public ledger

---

## Architecture

### High-Level Flow

```
┌──────────────┐
│   Outage     │
│   Detected   │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Ops Engineer │
│ Resolves     │
└──────┬───────┘
       │
       ▼
┌──────────────────┐
│ ApexChainx       │──► MTTR: 25 minutes
│ Calculates MTTR  │    Threshold: 15 minutes
└──────┬───────────┘    Status: VIOLATED
       │
       ▼
┌──────────────────────┐
│ Soroban Smart        │──► Penalty: $1,000
│ Contract Invoked     │    (10 min × $100/min)
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ Stellar Network      │──► Transaction submitted
│ Payment Executed     │    Confirmed in 3-5 seconds
└──────┬───────────────┘
       │
       ▼
┌──────────────────────┐
│ Notifications Sent   │──► Email, Webhook, UI update
│ Records Updated      │    Tx hash stored in Firestore
└──────────────────────┘
```

### Components

1. **ApexChainx Frontend**: User interface with wallet connection
2. **ApexChainx Backend**: FastAPI server with Stellar SDK
3. **Soroban Smart Contracts**: On-chain SLA calculation logic
4. **Stellar Network**: Blockchain for payments and storage
5. **Firestore**: Off-chain database for application data

---

## Prerequisites

### Required Accounts and Tools

1. **Stellar Account**
   - Create testnet account: [Stellar Laboratory](https://laboratory.stellar.org/#account-creator?network=test)
   - For mainnet: Use Freighter or other wallet

2. **Freighter Wallet** (for frontend)
   - Install: [freighter.app](https://freighter.app/)
   - Switch to Testnet during development

3. **Stellar SDK**
   - JavaScript: `npm install stellar-sdk`
   - Python: `pip install stellar-sdk`

4. **Soroban CLI** (for smart contracts)
   ```bash
   cargo install --locked soroban-cli
   ```

5. **USDC Token**
   - Testnet USDC: We'll provide the asset code
   - Mainnet USDC: `USDC:GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN`

---

## Setup

### 1. Environment Variables

**Frontend (.env.local):**
```env
# Stellar Network
VITE_STELLAR_NETWORK=testnet
VITE_STELLAR_HORIZON_URL=https://horizon-testnet.stellar.org
VITE_STELLAR_SOROBAN_RPC_URL=https://soroban-testnet.stellar.org

# Smart Contract Addresses
VITE_SLA_CONTRACT_ID=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
VITE_USDC_TOKEN_ADDRESS=CBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
VITE_APEXCHAINX_TOKEN_ADDRESS=CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
```

**Backend (.env):**
```env
# Stellar Configuration
STELLAR_NETWORK=testnet
STELLAR_HORIZON_URL=https://horizon-testnet.stellar.org
STELLAR_SOROBAN_RPC_URL=https://soroban-testnet.stellar.org

# Pool Wallet - NEVER commit secret keys to version control!
# Generate these using: stellar-sdk Keypair.random()
STELLAR_POOL_PUBLIC_KEY=GXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

# Smart Contract IDs - obtained after contract deployment
SLA_CONTRACT_ID=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
USDC_TOKEN_ADDRESS=CBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
APEXCHAINX_TOKEN_ADDRESS=CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

# Payment Settings
AUTO_PAYMENT_ENABLED=true
MAX_AUTO_PAYMENT_AMOUNT=10000
```

> **SECURITY WARNING**: Never commit secret keys to version control. Use environment variables or secure key management systems. The `STELLAR_POOL_SECRET_KEY` should be stored securely and never exposed in logs, documentation, or code examples.

### 2. Create Stellar Accounts

**SECURITY CRITICAL**: Handle secret keys with extreme care. Never expose them in logs, documentation, or share them electronically.

**Using Stellar Laboratory (Testnet):**

1. Go to [Stellar Laboratory](https://laboratory.stellar.org/#account-creator?network=test)
2. Click "Generate keypair"
3. **IMMEDIATELY SAVE the Secret Key securely** (starts with 'S') - store in password manager or hardware security module
4. Copy the **Public Key** (starts with 'G') for configuration
5. Click "Fund account" to get testnet XLM from Friendbot

**Create accounts with clear purposes:**
- **Pool Account**: For holding and distributing funds (highest security requirement)
- **Operator Account**: For testing penalty payments (medium security)

**NEVER**:
- Commit secret keys to version control
- Log secret keys in application logs
- Include secret keys in documentation examples
- Share secret keys via email, chat, or unencrypted channels
- Use the same keys for testnet and mainnet

### 3. Establish Trustlines (USDC)

Before receiving USDC, accounts must establish a trustline:

**Using Stellar Laboratory:**

1. Go to [Transaction Builder](https://laboratory.stellar.org/#txbuilder?network=test)
2. Source Account: Your public key
3. Operations → Add Operation → Change Trust
4. Asset Code: `USDC`
5. Issuer: `[USDC issuer public key]`
6. Sign and submit

**Using Stellar SDK (Python):**
```python
from stellar_sdk import Server, Keypair, TransactionBuilder, Network, Asset

server = Server("https://horizon-testnet.stellar.org")
source_keypair = Keypair.from_secret("SXXX...")

# Build trustline transaction
source_account = server.load_account(source_keypair.public_key)
transaction = (
    TransactionBuilder(
        source_account=source_account,
        network_passphrase=Network.TESTNET_NETWORK_PASSPHRASE,
        base_fee=100,
    )
    .append_change_trust_op(
        asset=Asset("USDC", "GXXX...")  # USDC issuer
    )
    .set_timeout(30)
    .build()
)

transaction.sign(source_keypair)
response = server.submit_transaction(transaction)
print(f"Transaction hash: {response['hash']}")
```

### 4. Deploy Smart Contracts

**Build contracts:**
```bash
cd contracts/sla_calculator
cargo build --target wasm32-unknown-unknown --release
```

**Deploy to testnet:**
```bash
# SECURITY: Use environment variable for secret key, never hardcode
export STELLAR_DEPLOYER_SECRET="your-actual-secret-key-here"

soroban contract deploy \
  --wasm target/wasm32-unknown-unknown/release/sla_calculator.wasm \
  --network testnet \
  --source-account "$STELLAR_DEPLOYER_SECRET"
```

**Initialize contract:**
```bash
soroban contract invoke \
  --id CCCC... \
  --network testnet \
  --source-account "$STELLAR_DEPLOYER_SECRET" \
  -- initialize \
  --admin GXXX... \
  --usdc_token CBBB... \
  --pool_address GXXX...
```

Save the contract ID to your `.env` files.

---

## Wallet Management

### Frontend: Connect Wallet

```typescript
import { isConnected, requestAccess, getPublicKey } from '@stellar/freighter-api';

async function connectWallet() {
  // Check if Freighter is installed
  const installed = await isConnected();
  if (!installed) {
    alert('Please install Freighter wallet');
    return;
  }
  
  // Request access
  const publicKey = await requestAccess();
  console.log('Connected:', publicKey);
  
  return publicKey;
}
```

### Backend: Create Wallet

```python
from stellar_sdk import Keypair

def create_wallet():
    """Create a new Stellar keypair for a user"""
    keypair = Keypair.random()

    # SECURITY: Never return or log the secret key
    # Only return the public key for wallet linking
    return {
        "public_key": keypair.public_key,
        "secret_key": "[REDACTED - Store securely server-side or use hardware security module]"
    }

# CORRECT implementation - only expose public key
def create_wallet_secure():
    """Create a new Stellar keypair"""
    keypair = Keypair.random()

    # Store secret key securely (database with encryption, HSM, etc.)
    # NEVER return it in API responses
    store_secret_key_securely(keypair.secret)

    return {
        "public_key": keypair.public_key,
        "message": "Wallet created. Secret key stored securely."
    }
```

> **SECURITY WARNING**: The above example shows what NOT to do. Secret keys should never be returned in API responses. Use secure key management systems and only expose public keys for wallet operations.

### Check Balance

```typescript
import { Server } from 'stellar-sdk';

async function getBalance(publicKey: string) {
  const server = new Server('https://horizon-testnet.stellar.org');
  const account = await server.loadAccount(publicKey);
  
  const balances = {};
  account.balances.forEach(balance => {
    if (balance.asset_type === 'native') {
      balances.XLM = balance.balance;
    } else {
      balances[balance.asset_code] = balance.balance;
    }
  });
  
  return balances;
}
```

---

## SLA Payment System

### How It Works

1. **Outage Detected**: Timer starts
2. **Engineer Resolves**: Timer stops, MTTR calculated
3. **Smart Contract Called**: Determines penalty or reward
4. **Payment Executed**: Automatic transfer on Stellar
5. **Notification Sent**: All parties notified

### SLA Thresholds

| Severity | Threshold | Penalty Rate | Base Reward |
|----------|-----------|--------------|-------------|
| Critical | 15 min    | $100/min     | $750        |
| High     | 30 min    | $50/min      | $750        |
| Medium   | 60 min    | $25/min      | $750        |
| Low      | 120 min   | $10/min      | $600        |

### Penalty Calculation

```
If MTTR > Threshold:
  Penalty = (MTTR - Threshold) × Penalty_Rate
  
Example:
  Severity: Critical
  MTTR: 25 minutes
  Threshold: 15 minutes
  Penalty Rate: $100/min
  
  Penalty = (25 - 15) × 100 = $1,000
```

### Reward Calculation

```
If MTTR ≤ Threshold:
  Performance % = (MTTR / Threshold) × 100
  
  If Performance < 50%:    Multiplier = 2.0 (Exceptional)
  If Performance < 75%:    Multiplier = 1.5 (Excellent)
  If Performance ≤ 100%:   Multiplier = 1.0 (Good)
  
  Reward = Base_Reward × Multiplier

Example:
  Severity: High
  MTTR: 10 minutes
  Threshold: 30 minutes
  Performance: 33% (< 50%)
  Base Reward: $750
  
  Reward = $750 × 2.0 = $1,500
```

### Backend Implementation

```python
from app.services.sla.sla_calculator import SLACalculator
from app.services.stellar.payment_service import PaymentService

async def process_outage_resolution(outage_id: str):
    """Process SLA payment after outage resolution"""
    
    # Get outage details
    outage = await get_outage(outage_id)
    
    # Calculate SLA result
    calculator = SLACalculator()
    sla_result = await calculator.calculate_sla_result(outage)
    
    # Invoke smart contract
    contract_result = await calculator.invoke_sla_contract(
        outage_id=outage["id"],
        severity=outage["severity"],
        mttr_minutes=sla_result["mttr_minutes"]
    )
    
    # Execute payment if auto-payment enabled
    if AUTO_PAYMENT_ENABLED:
        payment_service = PaymentService()
        payment = await payment_service.execute_sla_payment(
            sla_result=contract_result,
            operator_address=outage["operator_wallet"],
            ops_team_address=outage["ops_team_wallet"]
        )
        
        return {
            "sla_result": contract_result,
            "payment": payment
        }
```

---

## Smart Contracts

### SLA Calculator Contract

**Functions:**

1. **`initialize`**: Set up contract with admin and token addresses
2. **`calculate_sla`**: Calculate penalty or reward for an outage
3. **`execute_payment`**: Execute payment based on SLA result
4. **`get_config`**: Get current SLA configuration
5. **`update_config`**: Update SLA thresholds (admin only)

### Invoking Contract (Backend)

```python
import os
from stellar_sdk import SorobanServer, TransactionBuilder, Network
from stellar_sdk.soroban_rpc import GetTransactionStatus

async def invoke_sla_contract(outage_id: str, severity: str, mttr: int):
    """Invoke SLA calculator contract"""

    soroban_server = SorobanServer("https://soroban-testnet.stellar.org")

    # SECURITY: Load secret from environment, never hardcode
    source_secret = os.getenv("STELLAR_POOL_SECRET_KEY")
    if not source_secret:
        raise ValueError("STELLAR_POOL_SECRET_KEY environment variable not set")

    source_keypair = Keypair.from_secret(source_secret)

    # Build contract invocation
    source_account = server.load_account(source_keypair.public_key)

    transaction = (
        TransactionBuilder(source_account, Network.TESTNET_NETWORK_PASSPHRASE, base_fee=100)
        .append_invoke_contract_function_op(
            contract_id=os.getenv("SLA_CONTRACT_ID"),
            function_name="calculate_sla",
            parameters=[
                scval.to_symbol(outage_id),
                scval.to_uint32(severity_to_enum(severity)),
                scval.to_uint32(mttr)
            ]
        )
        .set_timeout(30)
        .build()
    )

    # Simulate first
    simulated = soroban_server.simulate_transaction(transaction)

    # Prepare and sign
    prepared = soroban_server.prepare_transaction(transaction, simulated)
    prepared.sign(source_keypair)

    # Submit
    response = soroban_server.send_transaction(prepared)

    # Wait for confirmation
    while True:
        status = soroban_server.get_transaction(response.hash)
        if status.status != GetTransactionStatus.NOT_FOUND:
            break
        await asyncio.sleep(1)

    # Parse result
    return parse_contract_result(status.return_value)
```

> **SECURITY NOTE**: Always load sensitive keys from environment variables or secure key management systems. Never hardcode or log secret keys.

---

## Testing on Testnet

### 1. Fund Test Accounts

```bash
# Get free testnet XLM from Friendbot
curl "https://friendbot.stellar.org?addr=GXXX..."
```

### 2. Test Wallet Connection

```typescript
// In browser console
import { requestAccess } from '@stellar/freighter-api';
const publicKey = await requestAccess();
console.log('Connected:', publicKey);
```

### 3. Test Payment

```python
# Python script to test payment
from app.services.stellar.payment_service import PaymentService

service = PaymentService(network="testnet")

# SECURITY: Load from environment, never pass as parameter
import os
source_secret = os.getenv("TEST_PAYMENT_SECRET_KEY")

result = await service.create_payment(
    source_secret=source_secret,  # Only use for testing with testnet keys
    destination="GXXX...",
    amount="10.00",
    asset_code="USDC"
)

print(f"Transaction hash: {result['tx_hash']}")
print(f"View on explorer: https://stellar.expert/explorer/testnet/tx/{result['tx_hash']}")
```

> **SECURITY WARNING**: Only use testnet keys for testing. Never use mainnet keys in test scripts. Consider using dedicated test accounts with minimal funds.

### 4. Test SLA Flow

```bash
# Create test outage
curl -X POST http://localhost:8000/api/v1/outages \
  -H "Content-Type: application/json" \
  -d '{
    "site_name": "Test Site",
    "severity": "critical",
    "detected_at": "2026-01-16T10:00:00Z"
  }'

# Mark as resolved (25 minutes later - SLA violated)
curl -X PUT http://localhost:8000/api/v1/outages/OUT001 \
  -H "Content-Type: application/json" \
  -d '{
    "status": "resolved",
    "resolved_at": "2026-01-16T10:25:00Z"
  }'

# Check SLA result
curl http://localhost:8000/api/v1/sla/status/OUT001
```

### 5. Verify on Blockchain

Visit [Stellar Expert](https://stellar.expert/explorer/testnet) and search for your transaction hash to see:
- Transaction details
- Payment amount
- Source and destination
- Timestamp
- Smart contract invocation (if applicable)

---

## Deployment to Mainnet

### ⚠️ Pre-Deployment Checklist

- [ ] All tests passing on testnet
- [ ] Smart contracts audited
- [ ] Security review completed
- [ ] Key management strategy in place
- [ ] Backup and recovery procedures documented
- [ ] Monitoring and alerting configured
- [ ] Rate limiting and error handling tested
- [ ] User documentation complete

### 1. Update Environment Variables

Change network to `mainnet`:

```env
STELLAR_NETWORK=mainnet
STELLAR_HORIZON_URL=https://horizon.stellar.org
STELLAR_SOROBAN_RPC_URL=https://soroban.stellar.org
```

### 2. Deploy Contracts to Mainnet

```bash
soroban contract deploy \
  --wasm target/wasm32-unknown-unknown/release/sla_calculator.wasm \
  --network mainnet \
  --source-account SXXX...
```

**Note**: Mainnet deployment costs real XLM. Ensure you have sufficient balance.

### 3. Fund Pool Account

Transfer sufficient USDC to your pool account to cover expected payments.

### 4. Gradual Rollout

1. Start with **low-value transactions**
2. Enable for **selected customers** first
3. Monitor closely for **24-48 hours**
4. Gradually increase limits
5. Enable auto-payments for all

---

## Security Best Practices

### Key Management

**NEVER**:
- Commit secret keys to version control
- Log secret keys in application logs
- Include secret keys in documentation or examples
- Share secret keys via email, chat, or unencrypted channels
- Use the same keys for testnet and mainnet

**ALWAYS**:
- Use environment variables or secure key management systems (AWS KMS, HashiCorp Vault, etc.)
- Rotate keys regularly, especially after any suspected compromise
- Use hardware security modules (HSM) for production keys
- Implement proper access controls and audit logging for key operations
- Use separate accounts/keys for different environments (dev/testnet/mainnet)

### API Security

**Wallet Operations**:
- Never return secret keys in API responses
- Only expose public keys for wallet linking and balance checks
- Implement proper authentication and authorization for wallet operations
- Use rate limiting to prevent abuse

**Payment Operations**:
- Validate all payment amounts and destinations
- Implement idempotency to prevent duplicate payments
- Log all payment operations for audit purposes
- Use transaction monitoring and alerting

### Smart Contract Security

**Contract Deployment**:
- Audit contracts before mainnet deployment
- Use multisig for critical contract operations
- Implement proper access controls in contracts
- Test extensively on testnet before mainnet

**Contract Invocation**:
- Validate all inputs before sending to contracts
- Handle contract errors gracefully
- Implement retry logic with exponential backoff
- Monitor contract gas usage and costs

### Environment Separation

**Testnet vs Mainnet**:
- Never reuse keys between environments
- Use different contract addresses for each environment
- Implement environment-specific configuration validation
- Test all operations on testnet before mainnet deployment

### Monitoring and Alerting

**Implement monitoring for**:
- Failed transactions
- Unusual payment amounts
- Contract errors
- Key access patterns
- Balance changes

**Set up alerts for**:
- Large payment attempts
- Contract failures
- Key compromise indicators
- Balance anomalies

### Payments

**POST `/api/v1/payments/process-sla`**
```json
{
  "outage_id": "OUT001"
}
```

**GET `/api/v1/payments/history`**
```json
{
  "transactions": [
    {
      "tx_hash": "abc123...",
      "type": "penalty",
      "amount": 1000.00,
      "asset": "USDC",
      "status": "confirmed",
      "timestamp": "2026-01-16T10:30:00Z"
    }
  ]
}
```

### Wallets

**POST `/api/v1/wallets/create`**
```json
{
  "user_id": "user123"
}
```

**GET `/api/v1/wallets/{address}/balance`**
```json
{
  "XLM": 1000.00,
  "USDC": 5000.00,
  "APEXCHAINX": 500.00
}
```

### SLA

**GET `/api/v1/sla/status/{outage_id}`**
```json
{
  "status": "violated",
  "mttr_minutes": 25,
  "threshold_minutes": 15,
  "penalty_amount": 1000.00,
  "contract_tx_hash": "def456..."
}
```

---

## Troubleshooting

### Wallet Connection Issues

**Problem**: "Freighter not detected"
- **Solution**: Install Freighter browser extension from [freighter.app](https://freighter.app/)

**Problem**: "Wrong network"
- **Solution**: Open Freighter → Settings → Switch to Testnet (for development)

### Transaction Failures

**Problem**: "Transaction failed: Insufficient balance"
- **Solution**: Ensure account has enough XLM for fees (~0.00001 XLM per operation)

**Problem**: "Transaction failed: No trustline"
- **Solution**: Establish trustline for USDC before receiving payments

**Problem**: "Transaction timeout"
- **Solution**: Stellar network may be congested. Wait and retry, or increase timeout

### Smart Contract Issues

**Problem**: "Contract not found"
- **Solution**: Verify contract ID in environment variables. Ensure contract is deployed.

**Problem**: "Contract invocation failed"
- **Solution**: Check contract parameters. Ensure source account is authorized.

### Payment Issues

**Problem**: "Payment not executing"
- **Solution**: Check `AUTO_PAYMENT_ENABLED` flag. Verify wallet balances. Check logs for errors.

**Problem**: "Payment stuck in pending"
- **Solution**: Check transaction status on Stellar Explorer. May need to resubmit.

---

## FAQ

### General

**Q: Do I need XLM for transactions?**  
A: Yes, every Stellar transaction requires a small fee (~0.00001 XLM). Accounts must maintain a minimum balance of 1 XLM.

**Q: What's the difference between testnet and mainnet?**  
A: Testnet uses fake money for testing. Mainnet uses real assets with real value.

**Q: How long do transactions take?**  
A: Stellar transactions typically confirm in 3-5 seconds.

### Payments

**Q: What if a payment fails?**  
A: Failed payments are logged and can be retried. The system will attempt up to 3 retries automatically.

**Q: Can I cancel a payment?**  
A: No, Stellar transactions are final once confirmed. Always verify details before submitting.

**Q: What happens if there's insufficient balance?**  
A: Transaction will fail with "insufficient balance" error. Ensure pool account has adequate funds.

### Security

**Q: Where are private keys stored?**  
A: User private keys stay in their wallet (Freighter). Server private keys should be in secure vault (AWS Secrets Manager, Google Cloud KMS).

**Q: Can transactions be reversed?**  
A: No, blockchain transactions are irreversible. Always verify recipient addresses.

**Q: How do I backup my wallet?**  
A: In Freighter, go to Settings → Show Secret Key. Write it down and store securely offline.

### Smart Contracts

**Q: Can SLA thresholds be changed?**  
A: Yes, admins can update SLA configurations via the `update_config` function.

**Q: What if there's a bug in the contract?**  
A: Contracts should be audited before mainnet deployment. In case of issues, admin can pause automated payments.

**Q: How much does contract execution cost?**  
A: Soroban contract invocations cost a few cents in XLM, much cheaper than other blockchain platforms.

---

## Additional Resources

- [Stellar Documentation](https://developers.stellar.org/)
- [Soroban Documentation](https://soroban.stellar.org/docs)
- [Stellar SDK - JavaScript](https://stellar.github.io/js-stellar-sdk/)
- [Stellar SDK - Python](https://stellar-sdk.readthedocs.io/)
- [Stellar Expert Explorer](https://stellar.expert/)
- [Stellar Laboratory](https://laboratory.stellar.org/)
- [Freighter Wallet](https://freighter.app/)
- [Stellar Discord Community](https://discord.gg/stellardev)

---

**Need help?** Open an issue on GitHub or join our Discord community!
