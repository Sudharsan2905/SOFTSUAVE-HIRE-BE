import asyncio
import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone

MONGODB_URL = "mongodb://localhost:27017"
DATABASE_NAME = "softsuvehire"


async def seed():
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]

    email = "admin@softsuave.com"
    existing = await db.users.find_one({"email": email})
    if existing:
        print(f"Super admin already exists: {email}")
        client.close()
        return

    password = "Admin@123"
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    now = datetime.now(timezone.utc)
    await db.users.insert_one({
        "name": "Super Admin",
        "email": email,
        "password_hash": password_hash,
        "role": "super_admin",
        "created_at": now,
        "updated_at": now,
    })

    print("Super admin created successfully!")
    print(f"  Email   : {email}")
    print(f"  Password: Admin@123")
    client.close()


asyncio.run(seed())
