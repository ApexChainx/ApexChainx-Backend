# Contributing to ApexChainx

First off, thank you for considering contributing to ApexChainx! It's people like you that make ApexChainx such a great tool for service operators managing SLA compliance and outage resolution.

## 🌊 Participating in Stellar Wave

ApexChainx is part of the [Stellar Wave Program](https://www.drips.network/wave/stellar)! If you're here from the Wave:

1. **Browse Issues**: Look for issues tagged with `Stellar Wave`
2. **Apply to Work**: Comment on the issue you want to work on
3. **Get Assigned**: Wait for a maintainer to assign you
4. **Submit PR**: Create a pull request when ready

**Important**: Only one contributor per issue. First to apply and get assigned gets the work.

## 🤝 Ways to Contribute

There are many ways to contribute to ApexChainx:

- **Report bugs** and issues
- **Suggest new features** or enhancements
- **Fix bugs** and implement features
- **Improve documentation**
- **Write tests** to increase coverage
- **Review pull requests**
- **Help answer questions** in discussions

## 🚀 Getting Started

### Prerequisites

**For Frontend (apexchainx-fe):**
- Node.js 18.x or higher
- npm or yarn
- Git
- Freighter wallet (for Stellar features)

**For Backend (apexchainx-be):**
- Python 3.11 or higher
- pip and virtualenv
- Git

**For Smart Contracts (apexchainx-contracts):**
- Rust and Cargo
- Soroban CLI
- Stellar CLI

### Fork and Clone

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/apexchainx-fe.git
   # or
   git clone https://github.com/YOUR_USERNAME/apexchainx-be.git
   # or
   git clone https://github.com/YOUR_USERNAME/apexchainx-contracts.git
   ```
3. **Add upstream remote**:
   ```bash
   git remote add upstream https://github.com/ApexChainx/ApexChainx-Frontend.git
   ```

### Setup Development Environment

**Frontend:**
```bash
cd apexchainx-fe
npm install
cp .env.example .env.local
# Edit .env.local with your config
npm run dev
```

**Backend:**
```bash
cd apexchainx-be
python3 -m venv .venv
source .venv/bin/activate
# On Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
# Edit .env — never commit it
# Edit .env with your config
alembic upgrade head
uvicorn app.main:app --reload
```

**Smart Contracts:**
```bash
cd apexchainx-contracts
# Install Soroban CLI if you haven't
cargo install --locked soroban-cli
# Build contracts
make build
# Run tests
make test
```

## 📝 Development Workflow

### 1. Create a Branch

Always create a new branch for your work:

```bash
git checkout -b feature/wallet-integration
# or
git checkout -b fix/payment-bug
# or
git checkout -b docs/stellar-guide
```

**Branch naming convention:**
- `feature/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation
- `test/description` - Adding tests
- `refactor/description` - Code refactoring

### 2. Make Your Changes

- Write clean, readable code
- Follow the project's code style (see below)
- Add tests for new functionality
- Update documentation as needed
- Keep commits focused and atomic

### 3. Test Your Changes

**Frontend:**
```bash
npm run test
npm run lint
npm run type-check
```

**Backend:**
```bash
pytest
pytest -v
pytest --cov=app --cov-report=html
black app/          # auto-format
flake8 app/         # lint
mypy app/           # type-check
```

**Smart Contracts:**
```bash
cargo test
cargo clippy -- -D warnings
```

### 4. Commit Your Changes

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git commit -m "feat: add wallet balance display"
git commit -m "fix: resolve payment timeout issue"
git commit -m "docs: update stellar integration guide"
git commit -m "test: add unit tests for SLA calculator"
```

**Commit message format:**
```
<type>: <description>

[optional body]

[optional footer]
```

**Types:**

Scope is optional but encouraged: `feat(sla): add bulk recompute endpoint`

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, semicolons, etc.)
- `refactor`: Code refactoring without behaviour change
- `test`: Adding or updating tests
- `chore`: Maintenance tasks
- `perf`: Performance improvements
- `ci`: CI/CD configuration changes

### 5. Push and Create Pull Request

```bash
git push origin feature/wallet-integration
```

Then open a pull request on GitHub with:
- **Clear title** following conventional commit format (keep under 70 characters)
- **Description** of what you changed and why
- **Screenshots** (for UI changes)
- **Testing notes** (how you tested the changes)
- **Related issue**: `Closes #123` or `Fixes #456`

## 🎨 Code Style Guidelines

### Frontend (TypeScript/React)

- Use **TypeScript** for all new files
- Follow **React hooks** best practices
- Use **functional components** over class components
- Use **Tailwind CSS** for styling (no inline styles)
- Use **shadcn/ui** components when available
- **Extract reusable logic** into custom hooks
- **PropTypes or TypeScript interfaces** for all components

**Example:**
```typescript
import { useState } from 'react';
import { Button } from '@/components/ui/button';

interface WalletConnectProps {
  onConnect: (publicKey: string) => void;
}

export function WalletConnect({ onConnect }: WalletConnectProps) {
  const [connected, setConnected] = useState(false);
  
  // Component logic here
  
  return (
    <Button onClick={handleConnect}>
      {connected ? 'Disconnect' : 'Connect Wallet'}
    </Button>
  );
}
```

### Backend (Python/FastAPI)

- Follow **PEP 8** style guide
- Use **type hints** for all functions
- Write **docstrings** for all public functions
- Use **async/await** for I/O operations
- **Pydantic models** for request/response validation
- **Dependency injection** for services
- **Environment variables** for configuration

**Example:**
```python
from fastapi import APIRouter, Depends, HTTPException
from app.models.payment import PaymentCreate, PaymentResponse
from app.services.stellar.payment_service import PaymentService
from app.api.deps import get_current_user

router = APIRouter()

@router.post("/payments", response_model=PaymentResponse)
async def create_payment(
    payment: PaymentCreate,
    current_user = Depends(get_current_user)
) -> PaymentResponse:
    """
    Create a new payment transaction on Stellar network.
    
    Args:
        payment: Payment details including amount and destination
        current_user: Currently authenticated user
        
    Returns:
        PaymentResponse with transaction hash and status
        
    Raises:
        HTTPException: If payment creation fails
    """
    service = PaymentService()
    result = await service.create_payment(payment)
    return result
```

### Smart Contracts (Rust/Soroban)

- Follow **Rust best practices**
- **Document all public functions**
- Use **proper error handling**
- **Test all functions** thoroughly
- Keep **gas costs** in mind
- Use **clippy** for linting

**Example:**
```rust
#[contractimpl]
impl SLAContract {
    /// Calculate SLA result for an outage
    /// 
    /// # Arguments
    /// * `outage_id` - Unique identifier for the outage
    /// * `severity` - Severity level (Critical, High, Medium, Low)
    /// * `mttr_minutes` - Mean time to repair in minutes
    /// 
    /// # Returns
    /// SLAResult containing status and payment information
    pub fn calculate_sla(
        env: Env,
        outage_id: Symbol,
        severity: Severity,
        mttr_minutes: u32,
    ) -> SLAResult {
        // Implementation here
    }
}
```

## ✅ Pull Request Guidelines

### Before Submitting

- [ ] Code follows the style guidelines
- [ ] Self-review completed
- [ ] Tests added/updated and passing
- [ ] Documentation updated
- [ ] No console.log or print statements
- [ ] Environment variables in .env.example
- [ ] Breaking changes clearly documented

### PR Description Template

```markdown
## Description
Brief description of the changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Related Issue
Closes #123

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] Tests pass locally

## Screenshots (if applicable)
[Add screenshots here]

## Additional Notes
Any additional information for reviewers
```

### For Stellar Wave Contributors

Include in your PR description:
- **Testnet transaction hashes** (for blockchain features)
- **Video/GIF** of feature working (for UI changes)
- **Performance metrics** (if relevant)
- **Time spent** on the issue (optional)

## 🧪 Testing Guidelines

### Frontend Tests

```bash
# Run all tests
npm run test

# Run tests in watch mode
npm run test:watch

# Run with coverage
npm run test:coverage
```

**Test structure:**
```typescript
import { render, screen, fireEvent } from '@testing-library/react';
import { WalletConnect } from './WalletConnect';

describe('WalletConnect', () => {
  it('should connect to Freighter wallet', async () => {
    render(<WalletConnect onConnect={jest.fn()} />);
    
    const button = screen.getByText('Connect Wallet');
    fireEvent.click(button);
    
    // Assertions here
  });
});
```

### Backend Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_payment_service.py

# Run with coverage
pytest --cov=app --cov-report=html
```

**Test structure:**
```python
import pytest
from app.services.stellar.payment_service import PaymentService

@pytest.mark.asyncio
async def test_create_payment():
    """Test payment creation on Stellar network"""
    service = PaymentService(network="testnet")
    
    result = await service.create_payment(
        source_secret="S...",
        destination="G...",
        amount="10.00"
    )
    
    assert result["status"] == "success"
    assert "tx_hash" in result
```

### Smart Contract Tests

```bash
# Run tests
cargo test

# Run with output
cargo test -- --nocapture
```

## 📚 Documentation Guidelines

- Use **clear, concise language**
- Include **code examples**
- Add **screenshots** for UI features
- Keep **up-to-date** with code changes
- **Link to related docs** where helpful
- Use **Markdown** for formatting

## 🔒 Security Guidelines

**CRITICAL: Security is everyone's responsibility. Follow these guidelines strictly.**

### Secret Management
- **Never commit secrets** (API keys, private keys, passwords, tokens) to version control
- Use **environment variables** or a secrets manager (AWS Secrets Manager, HashiCorp Vault) for sensitive data
- **Never log secrets** or include them in error messages, stack traces, or documentation
- Use **separate secrets** for each environment (dev/staging/prod)
- **Rotate secrets** regularly and immediately after any suspected compromise

### Authentication & Authorization
- Always use **bcrypt** for password hashing (never plaintext or weak hashes)
- Implement **rate limiting** on auth endpoints to prevent brute force attacks
- Use **token rotation** for refresh tokens to detect replay attacks
- Follow the **principle of least privilege** for all API endpoints
- Validate **role-based access** on every protected endpoint

### Input Validation & Sanitization
- **Validate all inputs** using Pydantic models with strict type checking
- Enforce **payload size limits** to prevent abuse (see MAX_REQUEST_BODY_SIZE_BYTES)
- **Sanitize user inputs** before storage or processing
- Use **parameterized queries** for database operations (SQLAlchemy handles this)
- Implement **CORS policies** that restrict allowed origins

### Blockchain & Wallet Security
- **NEVER expose Stellar secret keys** (starting with 'S') in code, logs, or docs
- Only use **public keys** (starting with 'G') for wallet linking and balance queries
- **Separate testnet and mainnet keys** - never reuse across environments
- Use **hardware security modules (HSM)** or secure enclaves for production key storage
- Implement **transaction validation** before submission (amount, destination, asset type)

### Webhook Security
- Always **verify webhook signatures** using HMAC-SHA256 before processing
- Implement **idempotency** to prevent duplicate webhook processing
- Use **HTTPS** for all webhook endpoints
- Validate **webhook payload structure** before acting on events

### Audit Logging (BE-010)
- **Never log sensitive data** (passwords, tokens, secret keys)
- Include **actor attribution** (user ID/email) in all audit events
- Add **correlation IDs** to track requests across services
- Audit logs are **immutable** - never modify or delete audit entries
- Redact sensitive fields automatically using the audit service's sanitization logic

### Code Review Security Checklist
Before approving any PR, verify:
- [ ] No secrets or credentials in code or comments
- [ ] All user inputs are validated and sanitized
- [ ] Auth checks are present on protected endpoints
- [ ] Error messages don't leak sensitive information
- [ ] Dependencies are up-to-date and free of known vulnerabilities
- [ ] Audit logging captures security-relevant events


## 🐛 Reporting Bugs

Use the GitHub issue template and include:

- **Clear title** describing the bug
- **Steps to reproduce** the issue
- **Expected behavior**
- **Actual behavior**
- **Screenshots** (if applicable)
- **Environment details** (OS, browser, versions)
- **Error messages** (full stack trace if possible)
- **For Stellar issues**: Include network (testnet/mainnet) and transaction hash

## 💡 Suggesting Features

Use the GitHub issue template and include:

- **Clear title** describing the feature
- **Problem statement** (what problem does this solve?)
- **Proposed solution**
- **Alternative solutions** considered
- **Additional context** (mockups, examples, etc.)


## 📞 Getting Help

- **GitHub Issues**: For bugs and feature requests
- **Discord**: [Join our server] (link TBD)
- **Stellar Discord**: For Stellar-specific questions

## 📜 License

By contributing to ApexChainx, you agree that your contributions will be licensed under the MIT License.

## 🙏 Thank You!

Your contributions make ApexChainx better for everyone. We appreciate your time and effort!

---

**Happy coding! 🚀**

---

## Common Mistakes to Avoid

- Do not push directly to `main` — all changes must go through a PR
- Do not commit `.env` files — use `.env.example` for documentation
- Do not add floating version ranges to `requirements.txt` — pin exact versions
- Do not put business logic in route handlers — it belongs in services
- Do not expose private keys via API responses or logs
- Do not skip tests — all PRs require passing test suite

---

## Reporting a Bug

When filing a bug report, include:

1. **Steps to reproduce** — the exact sequence of actions
2. **Expected behaviour** — what should have happened
3. **Actual behaviour** — what actually happened
4. **Environment** — Python version, OS, database version
5. **Logs** — relevant error output (redact any secrets)

---

## Proposing a Feature

Before opening a feature request:

1. Check existing issues to avoid duplicates
2. Describe the problem the feature solves — not just the solution
3. Consider the scope: does this belong in the backend, frontend, or contracts?
4. Outline acceptance criteria — what does "done" look like?
