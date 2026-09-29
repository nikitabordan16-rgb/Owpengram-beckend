from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from supabase import create_client, Client
import os, hashlib, secrets, httpx
from typing import Optional

app = FastAPI(title="OwpenGram Backend API")

# Supabase клиент (заполни после регистрации)
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
ADMIN_SECRET = os.environ["ADMIN_SECRET"]  # твой секретный ключ админа

sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# Утилиты
# ==========================================

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_by_token(authorization: str = Header(...)):
    """Простая авторизация по токену (user_id:secret)"""
    try:
        token = authorization.replace("Bearer ", "")
        user_id = token.split(":")[0]
        res = sb.table("users").select("*").eq("id", user_id).single().execute()
        if not res.data:
            raise HTTPException(401, "Unauthorized")
        return res.data
    except Exception:
        raise HTTPException(401, "Unauthorized")

def require_admin(authorization: str = Header(...)):
    if authorization != f"Bearer {ADMIN_SECRET}":
        raise HTTPException(403, "Admin only")

# ==========================================
# Регистрация и авторизация
# ==========================================

class RegisterRequest(BaseModel):
    username: str
    display_name: str
    password: str
    custom_num: Optional[str] = None  # если хочешь назначить конкретный номер

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/auth/register")
async def register(body: RegisterRequest):
    """Регистрация нового пользователя"""
    existing = sb.table("users").select("id").eq("username", body.username).execute()
    if existing.data:
        raise HTTPException(400, "Username already taken")

    user_data = {
        "username": body.username,
        "display_name": body.display_name,
        "password_hash": hash_password(body.password),
        "star_balance": 0,
        "rating": 0,
    }
    if body.custom_num:
        user_data["num"] = body.custom_num

    res = sb.table("users").insert(user_data).execute()
    user = res.data[0]

    # Создаём запись рейтинга
    sb.table("ratings").insert({"user_id": user["id"]}).execute()

    # Токен = user_id:random_secret (упрощённо)
    token = f"{user['id']}:{secrets.token_hex(16)}"
    return {"token": token, "user": {
        "id": user["id"],
        "num": user["num"],
        "username": user["username"],
        "display_name": user["display_name"],
        "star_balance": 0,
    }}

@app.post("/auth/login")
async def login(body: LoginRequest):
    res = sb.table("users").select("*").eq("username", body.username).single().execute()
    if not res.data or res.data["password_hash"] != hash_password(body.password):
        raise HTTPException(401, "Wrong credentials")
    user = res.data
    token = f"{user['id']}:{secrets.token_hex(16)}"
    return {"token": token, "user": {
        "id": user["id"],
        "num": user["num"],
        "username": user["username"],
        "star_balance": user["star_balance"],
        "rating": user["rating"],
    }}

# ==========================================
# Кастомные номера (только ты как админ)
# ==========================================

class IssueNumRequest(BaseModel):
    username: str          # кому выдаём
    custom_num: str        # "#12345678" или любой формат
    note: Optional[str] = None

@app.post("/admin/issue-num")
async def issue_custom_num(body: IssueNumRequest, _=Depends(require_admin)):
    """Выдать кастомный номер пользователю"""
    user = sb.table("users").select("*").eq("username", body.username).single().execute()
    if not user.data:
        raise HTTPException(404, "User not found")

    # Проверяем что номер свободен
    existing = sb.table("users").select("id").eq("num", body.custom_num).execute()
    if existing.data:
        raise HTTPException(400, "Number already taken")

    # Обновляем номер
    sb.table("users").update({"num": body.custom_num}).eq("id", user.data["id"]).execute()

    # Логируем в реестр
    sb.table("num_registry").insert({
        "num": body.custom_num,
        "issued_to": user.data["id"],
        "note": body.note
    }).execute()

    return {"ok": True, "num": body.custom_num, "issued_to": body.username}

# ==========================================
# Звёзды
# ==========================================

class GrantStarsRequest(BaseModel):
    username: str
    amount: int
    reason: Optional[str] = "admin_grant"

@app.post("/admin/grant-stars")
async def grant_stars(body: GrantStarsRequest, _=Depends(require_admin)):
    """Выдать звёзды пользователю (только ты)"""
    user = sb.table("users").select("*").eq("username", body.username).single().execute()
    if not user.data:
        raise HTTPException(404, "User not found")

    new_balance = user.data["star_balance"] + body.amount
    sb.table("users").update({"star_balance": new_balance}).eq("id", user.data["id"]).execute()

    sb.table("star_transactions").insert({
        "user_id": user.data["id"],
        "amount": body.amount,
        "reason": body.reason,
    }).execute()

    return {"ok": True, "new_balance": new_balance}

@app.get("/stars/balance")
async def get_balance(user=Depends(get_user_by_token)):
    return {"balance": user["star_balance"], "num": user["num"]}

# ==========================================
# Гифты
# ==========================================

class AddGiftRequest(BaseModel):
    name: str
    description: Optional[str] = None
    lottie_url: str         # ссылка на .tgs или .json
    preview_url: Optional[str] = None
    price_stars: int = 1
    rarity: str = "common"  # common / rare / epic / legendary

@app.post("/admin/add-gift")
async def add_gift(body: AddGiftRequest, _=Depends(require_admin)):
    """Добавить новый гифт в библиотеку"""
    res = sb.table("gifts").insert(body.dict()).execute()
    return {"ok": True, "gift": res.data[0]}

@app.get("/gifts")
async def list_gifts():
    """Список всех доступных гифтов"""
    res = sb.table("gifts").select("*").eq("is_active", True).order("price_stars").execute()
    return {"gifts": res.data}

class SendGiftRequest(BaseModel):
    to_username: str
    gift_id: str
    message: Optional[str] = None

@app.post("/gifts/send")
async def send_gift(body: SendGiftRequest, sender=Depends(get_user_by_token)):
    """Отправить гифт другому пользователю"""
    # Получаем гифт
    gift = sb.table("gifts").select("*").eq("id", body.gift_id).single().execute()
    if not gift.data:
        raise HTTPException(404, "Gift not found")

    # Получаем получателя
    recipient = sb.table("users").select("*").eq("username", body.to_username).single().execute()
    if not recipient.data:
        raise HTTPException(404, "User not found")

    if recipient.data["id"] == sender["id"]:
        raise HTTPException(400, "Can't send gift to yourself")

    # Проверяем баланс
    price = gift.data["price_stars"]
    if sender["star_balance"] < price:
        raise HTTPException(400, f"Not enough stars. Need {price}, have {sender['star_balance']}")

    # Списываем звёзды у отправителя
    sb.table("users").update({
        "star_balance": sender["star_balance"] - price
    }).eq("id", sender["id"]).execute()

    # Пишем транзакцию
    sb.table("star_transactions").insert({
        "user_id": sender["id"],
        "amount": -price,
        "reason": "gift_sent",
    }).execute()

    # Добавляем в инвентарь получателя
    sb.table("inventory").insert({
        "owner_id": recipient.data["id"],
        "gift_id": body.gift_id,
        "received_from": sender["id"],
    }).execute()

    # Пишем в историю передач (триггер автоматически обновит рейтинг)
    sb.table("gift_transfers").insert({
        "from_user": sender["id"],
        "to_user": recipient.data["id"],
        "gift_id": body.gift_id,
        "stars_spent": price,
        "message": body.message,
    }).execute()

    return {"ok": True, "gift_name": gift.data["name"], "stars_spent": price}

@app.get("/inventory/{username}")
async def get_inventory(username: str):
    """Инвентарь пользователя"""
    user = sb.table("users").select("id").eq("username", username).single().execute()
    if not user.data:
        raise HTTPException(404, "User not found")

    res = sb.table("inventory").select(
        "*, gifts(*), sender:received_from(username, display_name, num)"
    ).eq("owner_id", user.data["id"]).order("received_at", desc=True).execute()

    return {"inventory": res.data}

# ==========================================
# Рейтинг
# ==========================================

@app.get("/rating/top")
async def get_top_rating(limit: int = 50):
    """Топ пользователей"""
    res = sb.table("ratings").select(
        "*, users(username, display_name, num, avatar_url)"
    ).order("score", desc=True).limit(limit).execute()

    # Добавляем места
    leaderboard = []
    for i, row in enumerate(res.data, 1):
        leaderboard.append({
            "rank": i,
            "username": row["users"]["username"],
            "display_name": row["users"]["display_name"],
            "num": row["users"]["num"],
            "avatar_url": row["users"]["avatar_url"],
            "score": row["score"],
            "gifts_sent": row["total_gifts_sent"],
            "gifts_received": row["total_gifts_received"],
            "stars_sent": row["total_stars_sent"],
        })

    return {"leaderboard": leaderboard}

@app.get("/rating/me")
async def my_rating(user=Depends(get_user_by_token)):
    res = sb.table("ratings").select("*").eq("user_id", user["id"]).single().execute()
    return res.data or {"score": 0, "rank": None}

# ==========================================
# Скачать гифты из Telegram (утилита)
# ==========================================

@app.get("/admin/tg-gifts")
async def list_tg_gifts(_=Depends(require_admin)):
    """
    Получить список официальных Telegram-гифтов через Bot API.
    Нужно добавить BOT_TOKEN в env.
    """
    bot_token = os.environ.get("TG_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(400, "Set TG_BOT_TOKEN env variable")

    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://api.telegram.org/bot{bot_token}/getAvailableGifts")
        data = res.json()

    if not data.get("ok"):
        raise HTTPException(500, "Telegram API error")

    gifts = []
    for gift in data["result"]["gifts"]:
        sticker = gift.get("sticker", {})
        gifts.append({
            "tg_id": gift.get("id"),
            "price_stars": gift.get("star_count", 1),
            "total_count": gift.get("total_count"),
            "remaining": gift.get("remaining_count"),
            "file_id": sticker.get("file_id"),
            "emoji": sticker.get("emoji"),
            "is_animated": sticker.get("is_animated"),
        })

    return {"gifts": gifts, "total": len(gifts)}

@app.get("/admin/tg-gift-url/{file_id}")
async def get_tg_gift_url(file_id: str, _=Depends(require_admin)):
    """Получить прямую ссылку на .tgs файл гифта из Telegram"""
    bot_token = os.environ.get("TG_BOT_TOKEN")
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={"file_id": file_id}
        )
        data = res.json()

    file_path = data["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    return {"url": url, "file_path": file_path}

@app.get("/")
async def root():
    return {"status": "ok", "service": "OwpenGram Backend"}
