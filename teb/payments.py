"""
Real financial integration module.

MVP payment API integration supporting:
  - Mercury (business banking: account balances, transfers)
  - Stripe (payment processing: payment intents, customers, balance)

Each provider implements a common interface so the executor and spending
approval system can execute real payments when configured.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from teb import config as _config
from teb import storage

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

MERCURY_API_KEY: str = _config.MERCURY_API_KEY or ""
MERCURY_BASE_URL: str = _config.MERCURY_BASE_URL

STRIPE_API_KEY: str = _config.STRIPE_API_KEY or ""
STRIPE_BASE_URL: str = _config.STRIPE_BASE_URL

_HTTP_TIMEOUT = 30


# ─── Provider interface ──────────────────────────────────────────────────────

class PaymentProvider:
    """Base interface for payment providers."""

    provider_name: str = "base"

    def get_balance(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch account balance. Returns {available, currency, raw_response}."""
        raise NotImplementedError

    def create_transfer(self, config: Dict[str, Any], amount: float, currency: str,
                        recipient: str, description: str = "") -> Dict[str, Any]:
        """Initiate a transfer/payment. Returns {tx_id, status, amount, currency}."""
        raise NotImplementedError

    def list_transactions(self, config: Dict[str, Any], limit: int = 25) -> List[Dict[str, Any]]:
        """List recent transactions."""
        raise NotImplementedError


# ─── Mercury Provider ─────────────────────────────────────────────────────────

class MercuryProvider(PaymentProvider):
    """Mercury business banking API integration.

    Requires TEB_MERCURY_API_KEY (Bearer token).
    Config should include 'account_id' for the Mercury bank account to use.

    Docs: https://docs.mercury.com/reference
    """

    provider_name = "mercury"

    def _headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        api_key = config.get("api_key") or MERCURY_API_KEY
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_balance(self, config: Dict[str, Any]) -> Dict[str, Any]:
        account_id = config.get("account_id", "")
        if not account_id:
            return {"error": "account_id required in config", "available": 0, "currency": "USD"}
        try:
            resp = httpx.get(
                f"{MERCURY_BASE_URL}/account/{account_id}",
                headers=self._headers(config),
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "available": data.get("availableBalance", 0),
                    "current": data.get("currentBalance", 0),
                    "currency": "USD",
                    "account_name": data.get("name", ""),
                    "raw_response": data,
                }
            return {"error": f"Mercury API {resp.status_code}: {resp.text}", "available": 0, "currency": "USD"}
        except httpx.HTTPError as e:
            logger.error("Mercury balance error: %s", e)
            return {"error": str(e), "available": 0, "currency": "USD"}

    def list_accounts(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """List all Mercury accounts."""
        try:
            resp = httpx.get(
                f"{MERCURY_BASE_URL}/accounts",
                headers=self._headers(config),
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                accounts = data.get("accounts", data) if isinstance(data, dict) else data
                return [
                    {
                        "id": a.get("id", ""),
                        "name": a.get("name", ""),
                        "available_balance": a.get("availableBalance", 0),
                        "current_balance": a.get("currentBalance", 0),
                        "kind": a.get("kind", ""),
                    }
                    for a in (accounts if isinstance(accounts, list) else [])
                ]
            return [{"error": f"Mercury API {resp.status_code}"}]
        except httpx.HTTPError as e:
            logger.error("Mercury list_accounts error: %s", e)
            return [{"error": str(e)}]

    def create_transfer(self, config: Dict[str, Any], amount: float, currency: str,
                        recipient: str, description: str = "") -> Dict[str, Any]:
        """Create a Mercury ACH/wire transfer.

        recipient should be a JSON string with at least:
          - routing_number
          - account_number
          - name (recipient name)
        """
        account_id = config.get("account_id", "")
        if not account_id:
            return {"error": "account_id required", "status": "failed"}
        try:
            recipient_data = json.loads(recipient) if isinstance(recipient, str) else recipient
        except (json.JSONDecodeError, TypeError):
            recipient_data = {"name": recipient}

        import hashlib
        stable_key = hashlib.sha256(
            f"{account_id}:{amount}:{recipient_data.get('account_number', '')}:{recipient_data.get('name', '')}".encode()
        ).hexdigest()[:32]

        payload = {
            "amount": amount,
            "payee": {
                "name": recipient_data.get("name", ""),
                "accountNumber": recipient_data.get("account_number", ""),
                "routingNumber": recipient_data.get("routing_number", ""),
            },
            "note": description,
            "idempotencyKey": f"teb-{stable_key}",
        }

        try:
            resp = httpx.post(
                f"{MERCURY_BASE_URL}/account/{account_id}/transactions",
                headers=self._headers(config),
                json=payload,
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "tx_id": data.get("id", ""),
                    "status": data.get("status", "pending"),
                    "amount": amount,
                    "currency": currency,
                    "raw_response": data,
                }
            return {"error": f"Mercury API {resp.status_code}: {resp.text}", "status": "failed"}
        except httpx.HTTPError as e:
            logger.error("Mercury transfer error: %s", e)
            return {"error": str(e), "status": "failed"}

    def list_transactions(self, config: Dict[str, Any], limit: int = 25) -> List[Dict[str, Any]]:
        account_id = config.get("account_id", "")
        if not account_id:
            return [{"error": "account_id required"}]
        try:
            resp = httpx.get(
                f"{MERCURY_BASE_URL}/account/{account_id}/transactions",
                headers=self._headers(config),
                params={"limit": limit},
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                txs = data.get("transactions", data) if isinstance(data, dict) else data
                return [
                    {
                        "id": t.get("id", ""),
                        "amount": t.get("amount", 0),
                        "status": t.get("status", ""),
                        "description": t.get("bankDescription", t.get("note", "")),
                        "date": t.get("postedAt", t.get("createdAt", "")),
                    }
                    for t in (txs if isinstance(txs, list) else [])
                ]
            return [{"error": f"Mercury API {resp.status_code}"}]
        except httpx.HTTPError as e:
            logger.error("Mercury list_transactions error: %s", e)
            return [{"error": str(e)}]


# ─── Stripe Provider ─────────────────────────────────────────────────────────

class StripeProvider(PaymentProvider):
    """Stripe payment processing API integration.

    Requires TEB_STRIPE_API_KEY (secret key).

    Docs: https://stripe.com/docs/api
    """

    provider_name = "stripe"

    def _headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        api_key = config.get("api_key") or STRIPE_API_KEY
        return {
            "Authorization": f"Bearer {api_key}",
        }

    def get_balance(self, config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = httpx.get(
                f"{STRIPE_BASE_URL}/balance",
                headers=self._headers(config),
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                available = data.get("available", [{}])
                total_available = sum(a.get("amount", 0) for a in available) / 100  # cents -> dollars
                currency = available[0].get("currency", "usd") if available else "usd"
                return {
                    "available": total_available,
                    "currency": currency.upper(),
                    "raw_response": data,
                }
            return {"error": f"Stripe API {resp.status_code}: {resp.text}", "available": 0, "currency": "USD"}
        except httpx.HTTPError as e:
            logger.error("Stripe balance error: %s", e)
            return {"error": str(e), "available": 0, "currency": "USD"}

    def create_transfer(self, config: Dict[str, Any], amount: float, currency: str,
                        recipient: str, description: str = "") -> Dict[str, Any]:
        """Create a Stripe PaymentIntent.

        For MVP this creates a PaymentIntent that can be confirmed by the frontend
        or used for server-to-server payments with an existing payment method.
        """
        try:
            payload = {
                "amount": int(amount * 100),  # dollars -> cents
                "currency": currency.lower(),
                "description": description,
                "metadata[teb_recipient]": recipient,
                "metadata[source]": "teb",
            }
            # If config has a payment_method, attach and auto-confirm
            if config.get("payment_method"):
                payload["payment_method"] = config["payment_method"]
                payload["confirm"] = "true"

            resp = httpx.post(
                f"{STRIPE_BASE_URL}/payment_intents",
                headers=self._headers(config),
                data=payload,  # Stripe uses form-encoded
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "tx_id": data.get("id", ""),
                    "status": data.get("status", "created"),
                    "amount": amount,
                    "currency": currency,
                    "client_secret": data.get("client_secret", ""),
                    "raw_response": data,
                }
            return {"error": f"Stripe API {resp.status_code}: {resp.text}", "status": "failed"}
        except httpx.HTTPError as e:
            logger.error("Stripe transfer error: %s", e)
            return {"error": str(e), "status": "failed"}

    def create_customer(self, config: Dict[str, Any], email: str, name: str = "") -> Dict[str, Any]:
        """Create a Stripe customer."""
        try:
            payload = {"email": email}
            if name:
                payload["name"] = name
            resp = httpx.post(
                f"{STRIPE_BASE_URL}/customers",
                headers=self._headers(config),
                data=payload,
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"customer_id": data.get("id", ""), "email": email, "raw_response": data}
            return {"error": f"Stripe API {resp.status_code}: {resp.text}"}
        except httpx.HTTPError as e:
            logger.error("Stripe create_customer error: %s", e)
            return {"error": str(e)}

    def list_transactions(self, config: Dict[str, Any], limit: int = 25) -> List[Dict[str, Any]]:
        """List recent Stripe charges."""
        try:
            resp = httpx.get(
                f"{STRIPE_BASE_URL}/charges",
                headers=self._headers(config),
                params={"limit": limit},
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                return [
                    {
                        "id": ch.get("id", ""),
                        "amount": ch.get("amount", 0) / 100,
                        "status": ch.get("status", ""),
                        "description": ch.get("description", ""),
                        "date": ch.get("created", ""),
                    }
                    for ch in data.get("data", [])
                ]
            return [{"error": f"Stripe API {resp.status_code}"}]
        except httpx.HTTPError as e:
            logger.error("Stripe list_transactions error: %s", e)
            return [{"error": str(e)}]


# ─── Provider Registry ────────────────────────────────────────────────────────

_PROVIDERS: Dict[str, PaymentProvider] = {
    "mercury": MercuryProvider(),
    "stripe": StripeProvider(),
}


def get_provider(name: str) -> Optional[PaymentProvider]:
    """Get a payment provider by name."""
    return _PROVIDERS.get(name.lower())


def list_providers() -> List[Dict[str, Any]]:
    """List available payment providers and their configuration status."""
    return [
        {
            "name": p.provider_name,
            "configured": _is_configured(p.provider_name),
        }
        for p in _PROVIDERS.values()
    ]


def _is_configured(provider_name: str) -> bool:
    """Check if a provider has its API key configured."""
    if provider_name == "mercury":
        return bool(MERCURY_API_KEY)
    if provider_name == "stripe":
        return bool(STRIPE_API_KEY)
    return False


# ─── High-level API ───────────────────────────────────────────────────────────

def execute_payment(user_id: int, provider_name: str, amount: float,
                    currency: str, recipient: str, description: str,
                    spending_request_id: Optional[int] = None) -> Dict[str, Any]:
    """Execute a real payment through a configured provider.

    This is the main entry point for the spending system to actually move money.

    Args:
        user_id: Owner of the payment account
        provider_name: 'mercury' or 'stripe'
        amount: Amount to transfer
        currency: Currency code (e.g. 'USD')
        recipient: Recipient info (provider-specific)
        description: Payment description
        spending_request_id: Optional link to a spending_request

    Returns:
        Dict with transaction details or error
    """
    provider = get_provider(provider_name)
    if not provider:
        return {"error": f"Unknown provider: {provider_name}", "status": "failed"}

    # Find the user's payment account for this provider
    accounts = storage.list_payment_accounts(user_id)
    account = next((a for a in accounts if a["provider"] == provider_name and a["enabled"]), None)
    if not account:
        return {"error": f"No {provider_name} account configured. Add one via POST /api/payments/accounts.",
                "status": "failed"}

    config = json.loads(account["config_json"]) if account.get("config_json") else {}

    # Create a local transaction record
    tx = storage.create_payment_transaction(
        account_id=account["id"],
        spending_request_id=spending_request_id,
        amount=amount,
        currency=currency,
        description=description,
    )

    # Execute via provider
    result = provider.create_transfer(config, amount, currency, recipient, description)

    if result.get("error"):
        storage.update_payment_transaction(
            tx["id"],
            status="failed",
            provider_response=json.dumps({"error": result["error"]}),
        )
        return {"error": result["error"], "status": "failed", "transaction_id": tx["id"]}

    # Update transaction with provider response
    storage.update_payment_transaction(
        tx["id"],
        status=result.get("status", "pending"),
        provider_tx_id=result.get("tx_id", ""),
        provider_response=json.dumps(result.get("raw_response", {})),
    )

    return {
        "status": result.get("status", "pending"),
        "transaction_id": tx["id"],
        "provider_tx_id": result.get("tx_id", ""),
        "amount": amount,
        "currency": currency,
        "provider": provider_name,
    }


def get_account_balance(user_id: int, provider_name: str) -> Dict[str, Any]:
    """Get the balance for a user's payment account."""
    provider = get_provider(provider_name)
    if not provider:
        return {"error": f"Unknown provider: {provider_name}"}

    accounts = storage.list_payment_accounts(user_id)
    account = next((a for a in accounts if a["provider"] == provider_name and a["enabled"]), None)
    if not account:
        return {"error": f"No {provider_name} account configured"}

    config = json.loads(account["config_json"]) if account.get("config_json") else {}
    return provider.get_balance(config)
