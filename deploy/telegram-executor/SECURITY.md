# Security contract

Only the RU egress certificate may call the executor. mTLS is mandatory;
firewall rules are defense in depth. No arbitrary URL or provider write API is
exposed. Identity bootstrap bundles are short-lived and encrypted to the
recipient public key; plaintext sessions and keys must never be logged.
