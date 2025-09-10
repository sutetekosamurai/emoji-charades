from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint     #Unique制約用

class GameStatus(str, Enum):
    lobby = "lobby"
    hint = "hint"
    vote = "vote"
    result = "result"

class Room(SQLModel, table=True):
    code: str = Field(primary_key=True, index=True)
    status: GameStatus = Field(default=GameStatus.lobby)
    round: int = 0
    lang: str = "ja"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    hint_deadline: Optional[datetime] = None
    vote_deadline: Optional[datetime] = None

class Player(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("room_code", "name", name="uq_player_name_per_room"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    room_code: str = Field(foreign_key="room.code", index=True)
    name: str
    is_host: bool = False
    score: int = 0
    connected_at: datetime = Field(default_factory=datetime.utcnow)

class Round(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    room_code: str = Field(foreign_key="room.code", index=True)
    topic: str = ""                     # 多数派お題
    spy_topic: str = ""                 # 少数派お題
    spy_player_id: Optional[int] = Field(default=None, foreign_key="player.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Hint(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("round_id", "player_id", name="uq_one_hint_per_round"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    round_id: int = Field(foreign_key="round.id", index=True)
    player_id: int = Field(foreign_key="player.id")
    content_emoji: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Vote(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("round_id", "voter_id", name="uq_one_vote_per_round"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    round_id: int = Field(foreign_key="round.id", index=True)
    voter_id: int = Field(foreign_key= "player.id")
    target_player_id: int = Field(foreign_key="player.id")
