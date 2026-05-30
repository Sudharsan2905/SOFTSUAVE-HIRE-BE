from typing import Annotated, cast

from fastapi import Depends, Request
from motor.motor_asyncio import AsyncIOMotorDatabase


def get_db(request: Request) -> AsyncIOMotorDatabase:
    return cast(AsyncIOMotorDatabase, request.app.state.db)


DB = Annotated[AsyncIOMotorDatabase, Depends(get_db)]
