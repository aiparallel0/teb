"""
Real financial integration module.

MVP payment API integration supporting:
  - Mercury (business banking: account balances, transfers)
  - Stripe (payment processing: payment intents, customers, balance)

Each provider implements a common interface so the executor and spending
approval system can execute real payments when configured.

Hardening features:
  - Retry with exponential backoff on transient errors (429, 5xx, timeouts)
  - Balance verification before executing transfers
  - Webhook endpoints for payment reconciliation
  - Failed transaction recovery with automatic retry
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
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
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # seconds; doubles each attempt
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


# ─── Retry helper ─────────────────────────────────────────────────────────────

def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    timeout: int = _HTTP_TIMEOUT,
    max_retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an HTTP request with exponential backoff retry on transient errors.

    Retries on 429, 5xx status codes and network timeouts.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            if method.upper() == "GET":
                resp = httpx.get(url, headers=headers, timeout=timeout, **kwargs)
            else:
                resp = httpx.post(url, headers=headers, timeout=timeout, **kwargs)

            if resp.status_code not in _RETRYABLE_STATUS_CODES or attempt == max_retries:
                return resp

            logger.warning(
                "Retryable status %d from %s (attempt %d/%d)",
                resp.status_code, url, attempt + 1, max_retries + 1,
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt == max_retries:
                raise
            logger.warning(
                "Transient error from %s (attempt %d/%d): %s",
                url, attempt + 1, max_retries + 1, exc,
            )

        delay = _RETRY_BACKOFF_BASE * (2 ** attempt)
        time.sleep(delay)

    # Should not reach here, but satisfy type checker
    if last_exc:
        raise last_exc
    raise httpx.HTTPError(f"Exhausted retries for {url}")  # pragma: no cover


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

    def verify_webhook(self, payload: bytes, signature: str, secret: str) -> bool:
        """Verify webhook signature. Override per provider."""
        return False


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
            resp = _request_with_retry(
                "GET",
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
            resp = _request_with_retry(
                "GET",
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
            resp = _request_with_retry(
                "POST",
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
            resp = _request_with_retry(
                "GET",
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

    def verify_webhook(self, payload: bytes, signature: str, secret: str) -> bool:
        """Verify Mercury webhook signature (HMAC-SHA256)."""
        if not secret:
            return False
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


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
            resp = _request_with_retry(
                "GET",
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
        Uses Idempotency-Key header to prevent duplicate charges on retries.
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

            # Stripe idempotency key: deterministic hash of amount + recipient + description
            idem_input = f"{amount}:{currency}:{recipient}:{description}".encode()
            idem_key = f"teb-{hashlib.sha256(idem_input).hexdigest()[:32]}"
            headers = {**self._headers(config), "Idempotency-Key": idem_key}

            resp = _request_with_retry(
                "POST",
                f"{STRIPE_BASE_URL}/payment_intents",
                headers=headers,
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
            resp = _request_with_retry(
                "POST",
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
            resp = _request_with_retry(
                "GET",
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

    def verify_webhook(self, payload: bytes, signature: str, secret: str) -> bool:
        """Verify Stripe webhook signature (v1 HMAC-SHA256)."""
        if not secret or not signature:
            return False
        parts = dict(item.split("=", 1) for item in signature.split(",") if "=" in item)
        timestamp = parts.get("t", "")
        v1_sig = parts.get("v1", "")
        if not timestamp or not v1_sig:
            return False
        signed_payload = f"{timestamp}.".encode() + payload
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1_sig)


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
                    spending_request_id: Optional[int] = None,
                    skip_balance_check: bool = False) -> Dict[str, Any]:
    """Execute a real payment through a configured provider.

    This is the main entry point for the spending system to actually move money.

    Performs balance verification before executing the transfer (unless
    skip_balance_check=True). On transient failures the provider layer
    automatically retries with exponential backoff (idempotency keys keep
    the operation safe).

    Args:
        user_id: Owner of the payment account
        provider_name: 'mercury' or 'stripe'
        amount: Amount to transfer
        currency: Currency code (e.g. 'USD')
        recipient: Recipient info (provider-specific)
        description: Payment description
        spending_request_id: Optional link to a spending_request
        skip_balance_check: If True, skip balance verification

    Returns:
        Dict with transaction details or error
    """
    provider = get_provider(provider_name)
    if not provider:
        return {"error": f"Unknown provider: {provider_name}", "status": "failed"}

    if amount <= 0:
        return {"error": "Amount must be positive", "status": "failed"}

    # Find the user's payment account for this provider
    accounts = storage.list_payment_accounts(user_id)
    account = next((a for a in accounts if a["provider"] == provider_name and a["enabled"]), None)
    if not account:
        return {"error": f"No {provider_name} account configured. Add one via POST /api/payments/accounts.",
                "status": "failed"}

    acct_config = json.loads(account["config_json"]) if account.get("config_json") else {}

    # ── Balance verification ──────────────────────────────────────────────
    if not skip_balance_check:
        balance = provider.get_balance(acct_config)
        if balance.get("error"):
            logger.warning("Balance check failed for %s: %s", provider_name, balance["error"])
            return {"error": f"Balance check failed: {balance['error']}", "status": "failed"}
        available = balance.get("available", 0)
        if available < amount:
            return {
                "error": f"Insufficient balance: {available} {currency} available, "
                         f"{amount} {currency} required",
                "status": "failed",
            }

    # Create a local transaction record
    tx = storage.create_payment_transaction(
        account_id=account["id"],
        spending_request_id=spending_request_id,
        amount=amount,
        currency=currency,
        description=description,
    )

    # Execute via provider (retry logic is inside provider methods)
    result = provider.create_transfer(acct_config, amount, currency, recipient, description)

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


# ─── Webhook Handling ─────────────────────────────────────────────────────────

# Provider webhook secrets (set via environment)
_WEBHOOK_SECRETS: Dict[str, str] = {
    "mercury": _config.MERCURY_API_KEY or "",  # Mercury uses API key for HMAC
    "stripe": _config.STRIPE_API_KEY or "",
}


def process_webhook(provider_name: str, payload: bytes, signature: str) -> Dict[str, Any]:
    """Process an incoming payment webhook from a provider.

    Verifies the webhook signature, then reconciles the event with local
    transaction records.

    Returns:
        Dict with reconciliation result
    """
    provider = get_provider(provider_name)
    if not provider:
        return {"error": f"Unknown provider: {provider_name}"}

    secret = _WEBHOOK_SECRETS.get(provider_name, "")
    if secret and not provider.verify_webhook(payload, signature, secret):
        logger.warning("Invalid webhook signature for %s", provider_name)
        return {"error": "Invalid webhook signature"}

    try:
        event = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return {"error": "Invalid webhook payload"}

    return _reconcile_webhook_event(provider_name, event)


def _reconcile_webhook_event(provider_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Reconcile a webhook event with local transaction records.

    Maps provider-specific event formats to our transaction model and
    updates local state accordingly.
    """
    provider_tx_id = ""
    new_status = ""
    raw_data = event

    if provider_name == "mercury":
        # Mercury events carry transaction data directly
        tx_data = event.get("data", event)
        provider_tx_id = tx_data.get("id", "")
        mercury_status = tx_data.get("status", "")
        new_status = _normalize_status(mercury_status)
    elif provider_name == "stripe":
        # Stripe events have type + data.object structure
        event_type = event.get("type", "")
        obj = event.get("data", {}).get("object", {})
        provider_tx_id = obj.get("id", "")
        if "succeeded" in event_type:
            new_status = "completed"
        elif "failed" in event_type:
            new_status = "failed"
        elif "canceled" in event_type:
            new_status = "cancelled"
        elif "pending" in event_type or "processing" in event_type:
            new_status = "pending"
        else:
            new_status = obj.get("status", "pending")

    if not provider_tx_id:
        return {"error": "No transaction ID in webhook event", "event": event}

    # Find and update matching local transaction
    updated = storage.reconcile_transaction_by_provider_id(
        provider_tx_id=provider_tx_id,
        status=new_status,
        provider_response=json.dumps(raw_data),
    )

    if updated:
        logger.info(
            "Reconciled %s transaction %s → %s",
            provider_name, provider_tx_id, new_status,
        )
        return {"reconciled": True, "provider_tx_id": provider_tx_id, "status": new_status}

    logger.warning(
        "Webhook for %s transaction %s: no matching local record",
        provider_name, provider_tx_id,
    )
    return {"reconciled": False, "provider_tx_id": provider_tx_id, "reason": "no matching transaction"}


def _normalize_status(provider_status: str) -> str:
    """Map provider-specific statuses to our canonical set."""
    status_lower = provider_status.lower()
    if status_lower in ("completed", "sent", "succeeded", "paid"):
        return "completed"
    if status_lower in ("failed", "rejected", "declined"):
        return "failed"
    if status_lower in ("cancelled", "canceled", "voided"):
        return "cancelled"
    return "pending"


# ─── Failed Transaction Recovery ─────────────────────────────────────────────

_MAX_RECOVERY_RETRIES = 3


def recover_failed_transactions() -> Dict[str, Any]:
    """Attempt to recover failed transactions by re-checking provider status.

    For each failed transaction that still has retries remaining:
    1. Query the provider for the actual transaction status
    2. If the provider says it succeeded, update our local record
    3. If still failed, increment retry count

    Returns summary of recovery actions taken.
    """
    failed_txs = storage.list_failed_transactions(max_retries=_MAX_RECOVERY_RETRIES)
    recovered = 0
    still_failed = 0
    errors = 0

    for tx in failed_txs:
        try:
            result = _recover_single_transaction(tx)
            if result == "recovered":
                recovered += 1
            elif result == "failed":
                still_failed += 1
            else:
                errors += 1
        except Exception:
            logger.exception("Error recovering transaction %s", tx.get("id"))
            errors += 1

    return {
        "total_checked": len(failed_txs),
        "recovered": recovered,
        "still_failed": still_failed,
        "errors": errors,
    }


def _recover_single_transaction(tx: Dict[str, Any]) -> str:
    """Attempt to recover a single failed transaction.

    Returns 'recovered', 'failed', or 'error'.
    """
    account = storage.get_payment_account(tx["account_id"])
    if not account:
        return "error"

    provider = get_provider(account["provider"])
    if not provider:
        return "error"

    acct_config = json.loads(account["config_json"]) if account.get("config_json") else {}
    provider_tx_id = tx.get("provider_tx_id", "")

    if provider_tx_id:
        # Check the provider for the real status
        transactions = provider.list_transactions(acct_config, limit=50)
        for ptx in transactions:
            if ptx.get("id") == provider_tx_id:
                real_status = _normalize_status(ptx.get("status", ""))
                if real_status == "completed":
                    storage.update_payment_transaction(
                        tx["id"],
                        status="completed",
                        provider_tx_id=provider_tx_id,
                        provider_response=json.dumps({"recovered": True, "provider_data": ptx}),
                    )
                    logger.info("Recovered transaction %s (provider says completed)", tx["id"])
                    return "recovered"
                break

    # Still failed — increment retry count
    storage.increment_transaction_retry(tx["id"])
    return "failed"
