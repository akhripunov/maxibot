def kb(*rows): return [[{"text":text,"callback_data":data} for text,data in row] for row in rows]
def main(): return kb(("📋 Мои каналы","channels"),("🤖 Мои боты","bots"))
def back(): return kb(("⬅️ Назад","home"))
