"""Owner-only WebAuthn ceremonies and opaque browser sessions.

The browser never receives the Caddy hop token. WebAuthn verification is
delegated to py_webauthn; this module only owns ceremony state and sessions.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import settings
from app.db.models import Admin, WebAdminAuthChallenge, WebAdminSession, WebAuthnCredential
from app.db.session import get_session

router = APIRouter(prefix="/admin/auth", tags=["web-admin-auth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _owner_query():
    owner_id = settings.maxibot_owner_user_id
    if not owner_id:
        raise HTTPException(status_code=503, detail="web admin owner is not configured")
    return select(Admin).where(Admin.external_id == str(owner_id))


async def _owner(db: AsyncSession) -> Admin:
    owner = await db.scalar(_owner_query())
    if not owner:
        raise HTTPException(status_code=503, detail="web admin owner is not registered")
    return owner


async def require_edge_token(
    x_maxibot_admin_token: str | None = Header(default=None, alias="X-MAXIBOT-Admin-Token"),
) -> None:
    configured = settings.web_admin_internal_token
    if not configured or not x_maxibot_admin_token or not secrets.compare_digest(x_maxibot_admin_token, configured):
        raise HTTPException(status_code=401, detail="web admin edge authorization required")


async def require_web_admin(
    request: Request,
    db: AsyncSession = Depends(get_session),
    _: None = Depends(require_edge_token),
) -> Admin:
    raw = request.cookies.get("maxibot_admin_session")
    if not raw:
        raise HTTPException(status_code=401, detail="web admin session required")
    now = _now()
    session = await db.scalar(
        select(WebAdminSession).where(
            WebAdminSession.token_hash == _digest(raw),
            WebAdminSession.revoked_at.is_(None),
            WebAdminSession.expires_at > now,
        )
    )
    if not session:
        raise HTTPException(status_code=401, detail="web admin session expired")
    admin = await db.get(Admin, session.admin_id)
    if not admin or str(admin.external_id) != str(settings.maxibot_owner_user_id):
        raise HTTPException(status_code=403, detail="web admin owner authorization required")
    return admin


def _cookie(response: Response, token: str, *, max_age: int) -> None:
    response.set_cookie(
        "maxibot_admin_session",
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.web_admin_cookie_secure,
        samesite="strict",
        path="/",
    )


async def _new_challenge(db: AsyncSession, *, admin_id: str | None, purpose: str, challenge: bytes) -> WebAdminAuthChallenge:
    row = WebAdminAuthChallenge(
        admin_id=admin_id,
        purpose=purpose,
        challenge=bytes_to_base64url(challenge),
        expires_at=_now() + timedelta(seconds=120),
    )
    db.add(row)
    await db.flush()
    return row


def _usable(row: WebAdminAuthChallenge | None, purpose: str) -> bool:
    return bool(row and row.purpose == purpose and row.used_at is None and row.expires_at > _now())


@router.get("/status", dependencies=[Depends(require_edge_token)])
async def auth_status(request: Request, db: AsyncSession = Depends(get_session)):
    owner = await _owner(db)
    credential_count = int(
        await db.scalar(
            select(func.count()).select_from(WebAuthnCredential).where(
                WebAuthnCredential.admin_id == owner.id,
                WebAuthnCredential.revoked_at.is_(None),
            )
        )
        or 0
    )
    authenticated = False
    if request.cookies.get("maxibot_admin_session"):
        try:
            await require_web_admin(request, db)
            authenticated = True
        except HTTPException:
            authenticated = False
    return {"enrolled": credential_count > 0, "authenticated": authenticated}


@router.post("/register/options", dependencies=[Depends(require_edge_token)])
async def registration_options(
    x_web_admin_bootstrap: str | None = Header(default=None, alias="X-WebAdmin-Bootstrap"),
    db: AsyncSession = Depends(get_session),
):
    owner = await _owner(db)
    existing = int(await db.scalar(select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.admin_id == owner.id, WebAuthnCredential.revoked_at.is_(None))) or 0)
    if existing:
        raise HTTPException(status_code=409, detail="owner passkey is already enrolled")
    expected_hash = settings.web_admin_passkey_bootstrap_token_hash
    if not expected_hash or not x_web_admin_bootstrap or not secrets.compare_digest(_digest(x_web_admin_bootstrap), expected_hash):
        raise HTTPException(status_code=403, detail="one-time passkey bootstrap required")
    options = generate_registration_options(
        rp_id=settings.web_admin_rp_id,
        rp_name=settings.web_admin_rp_name,
        user_name=str(owner.external_id),
        user_display_name="MAXIBOT owner",
        user_id=owner.id.encode(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PSS_SHA_256],
    )
    challenge = await _new_challenge(db, admin_id=owner.id, purpose="registration", challenge=options.challenge)
    await db.commit()
    return {"challenge_id": challenge.id, "publicKey": json.loads(options_to_json(options))}


@router.post("/register/verify", dependencies=[Depends(require_edge_token)])
async def registration_verify(payload: dict = Body(...), db: AsyncSession = Depends(get_session)):
    owner = await _owner(db)
    challenge = await db.get(WebAdminAuthChallenge, str(payload.get("challenge_id") or ""))
    if not _usable(challenge, "registration") or challenge.admin_id != owner.id:
        raise HTTPException(status_code=400, detail="invalid or expired registration ceremony")
    existing = int(await db.scalar(select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.admin_id == owner.id, WebAuthnCredential.revoked_at.is_(None))) or 0)
    if existing:
        raise HTTPException(status_code=409, detail="owner passkey is already enrolled")
    try:
        verification = verify_registration_response(
            credential=payload.get("credential") or {},
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=settings.web_admin_rp_id,
            expected_origin=settings.web_admin_origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid passkey registration") from exc
    db.add(WebAuthnCredential(
        admin_id=owner.id,
        credential_id=bytes_to_base64url(verification.credential_id),
        credential_public_key=bytes_to_base64url(verification.credential_public_key),
        sign_count=verification.sign_count,
    ))
    challenge.used_at = _now()
    await db.commit()
    return {"registered": True}


@router.post("/login/options", dependencies=[Depends(require_edge_token)])
async def login_options(db: AsyncSession = Depends(get_session)):
    owner = await _owner(db)
    credentials = list((await db.scalars(select(WebAuthnCredential).where(WebAuthnCredential.admin_id == owner.id, WebAuthnCredential.revoked_at.is_(None)))).all())
    if not credentials:
        raise HTTPException(status_code=409, detail="owner passkey is not enrolled")
    options = generate_authentication_options(
        rp_id=settings.web_admin_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id)) for item in credentials],
    )
    challenge = await _new_challenge(db, admin_id=owner.id, purpose="authentication", challenge=options.challenge)
    await db.commit()
    return {"challenge_id": challenge.id, "publicKey": json.loads(options_to_json(options))}


@router.post("/login/verify", dependencies=[Depends(require_edge_token)])
async def login_verify(payload: dict = Body(...), response: Response = None, db: AsyncSession = Depends(get_session)):
    owner = await _owner(db)
    challenge = await db.get(WebAdminAuthChallenge, str(payload.get("challenge_id") or ""))
    if not _usable(challenge, "authentication") or challenge.admin_id != owner.id:
        raise HTTPException(status_code=400, detail="invalid or expired authentication ceremony")
    credential_data = payload.get("credential") or {}
    raw_id = credential_data.get("rawId") or credential_data.get("id")
    credential_id = bytes_to_base64url(base64url_to_bytes(str(raw_id))) if raw_id else ""
    stored = await db.scalar(select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id, WebAuthnCredential.admin_id == owner.id, WebAuthnCredential.revoked_at.is_(None)))
    if not stored:
        raise HTTPException(status_code=401, detail="unknown passkey")
    try:
        verification = verify_authentication_response(
            credential=credential_data,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=settings.web_admin_rp_id,
            expected_origin=settings.web_admin_origin,
            credential_public_key=base64url_to_bytes(stored.credential_public_key),
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid passkey assertion") from exc
    stored.sign_count = verification.new_sign_count
    challenge.used_at = _now()
    raw_session = secrets.token_urlsafe(48)
    db.add(WebAdminSession(admin_id=owner.id, token_hash=_digest(raw_session), expires_at=_now() + timedelta(seconds=settings.web_admin_session_ttl_seconds)))
    await db.commit()
    _cookie(response, raw_session, max_age=settings.web_admin_session_ttl_seconds)
    return {"authenticated": True}


@router.post("/logout", dependencies=[Depends(require_edge_token)])
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get("maxibot_admin_session")
    if raw:
        session = await db.scalar(select(WebAdminSession).where(WebAdminSession.token_hash == _digest(raw), WebAdminSession.revoked_at.is_(None)))
        if session:
            session.revoked_at = _now()
            await db.commit()
    response.delete_cookie("maxibot_admin_session", path="/")
    return {"authenticated": False}
