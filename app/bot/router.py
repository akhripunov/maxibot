from app.domain.services import menu_text, channel_card_text, NO_CHANNELS, SETTINGS
class MenuRouter:
    def __init__(self,client,repo): self.client,self.repo=client,repo
    async def render(self,admin_id,screen,message_id=None,**kwargs):
        text,buttons=await self.repo.screen(admin_id,screen,**kwargs)
        if message_id:
            try: await self.client.edit_message(admin_id,message_id,text,buttons); return message_id
            except Exception: pass
        new=await self.client.send_message(admin_id,text,buttons); await self.repo.save_service_message(admin_id,new); return new
