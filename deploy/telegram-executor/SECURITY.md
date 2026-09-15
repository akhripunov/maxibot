# Security contract

Only the RU egress certificate may call the executor. mTLS is mandatory;
firewall rules are defense in depth. No arbitrary URL or provider write API is
exposed. Identity bootstrap bundles are short-lived and encrypted to the
recipient public key; plaintext sessions and keys must never be logged.

## MAXIBOT_TELEGRAM_IDENTITY_BUNDLE_V1

The transfer bundle is a versioned bootstrap artifact, never a runtime
credential store. It uses the maintained `cryptography` library: a fresh
`Fernet.generate_key()` data key encrypts the identity payload (authenticated
encryption with Fernet), and that key is wrapped for the intended recipient
with RSA-OAEP using SHA-256 for both the digest and MGF1 (`label=None`). The
recipient public-key SHA-256 fingerprint is authenticated in the envelope and
payload. Import requires the matching private key and re-encrypts the session
into the local `IdentityStore` with its master key.

V1 accepts only the exact format marker and `format_version=1`, with a UUID
`identity_ref`, non-empty owner manifest, timezone-aware `created_at` and
future `expires_at`, and a matching recipient fingerprint. Unknown versions,
missing or malformed fields, tampering, wrong recipients, expiry, corrupt
stores, and duplicate imports fail closed. Existing identities are never
overwritten implicitly; replacement requires an explicit `replace=True`
operation in trusted provisioning code.
