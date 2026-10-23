from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.menu import render_screen
from app.db.models import Admin, Base, Channel


@pytest.mark.asyncio
async def test_delete_confirmation_is_short_preserves_channel_title_and_existing_buttons():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with sessions() as db:
            admin = Admin(external_id="admin-1")
            db.add(admin)
            await db.flush()
            title = "Петербург 365 ❤️ [лето] & утро\nВечер"
            channel = Channel(
                admin_id=admin.id,
                external_id="channel-1",
                title=title,
                public_slug="channel-1",
            )
            db.add(channel)
            await db.flush()

            text, keyboard = await render_screen(db, admin, f"delete_confirm:{channel.id}")

            assert text == f"🗑 Удаление канала\n\nВы действительно хотите удалить «{title}» из MAXIBOT?"
            assert [button["text"] for row in keyboard for button in row] == [
                "🗑 Да, удалить",
                "❌ Отмена",
            ]
            assert [button["callback_data"] for row in keyboard for button in row] == [
                f"delete_channel_confirm:{channel.id}",
                f"delete_cancel:{channel.id}",
            ]

            # Rendering the confirmation is read-only; deletion remains a separate confirmation action.
            assert await db.scalar(select(Channel.id).where(Channel.id == channel.id)) == channel.id
    finally:
        await engine.dispose()
