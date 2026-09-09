import asyncio
import os
import secrets
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.db.base import Base
from app.db.session import engine,SessionLocal
from app.db.models import Admin,Channel,Conversation,Message
async def seed():
 if os.getenv("SEED_DEMO_DATA", "").lower() not in {"1", "true", "yes"}:
  return
 async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
 async with SessionLocal() as db:
  admin=await db.scalar(select(Admin).where(Admin.external_id=="demo"))
  if not admin: admin=Admin(external_id="demo"); db.add(admin); await db.flush()
  channels=[]
  for i,(title,slug) in enumerate((("Петербург 24","peterburg-24"),("Новости Фрязино","novosti-fryazino"))):
   ch=await db.scalar(select(Channel).where(Channel.external_id==f"channel-{i+1}"))
   if not ch:
    ch=await db.scalar(select(Channel).where(Channel.public_slug==slug))
   if not ch:
    ch=Channel(admin_id=admin.id,external_id=f"channel-{i+1}",title=title,public_slug=secrets.token_urlsafe(9)); db.add(ch); await db.flush()
   channels.append(ch)
  if not await db.scalar(select(Conversation).limit(1)):
   for i,(ch,name,text) in enumerate(((channels[0],"Анна","Как отправить предложение?"),(channels[0],"Иван","Добрый день!"),(channels[1],"Мария","Есть вопрос по публикации."))):
    conv=Conversation(channel_id=ch.id,user_external_id=f"user-{i+1}",user_name=name,unread=i%2+1); db.add(conv); await db.flush(); db.add(Message(conversation_id=conv.id,direction="in",text=text)); db.add(Message(conversation_id=conv.id,direction="out",text="Спасибо, сообщение получено."))
  await db.commit()
asyncio.run(seed())
