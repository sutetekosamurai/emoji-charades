from fastapi import FastAPI, Request, Form, HTTPException, Query
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, Response, HTMLResponse
from math import ceil
import os
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.db import init_db, engine
from app.models import Room, Round, Player, GameStatus, Hint, Vote
from starlette.responses import RedirectResponse
from sqlmodel import Session, select, col
from sqlalchemy import inspect, func
import random, string
from collections import Counter
from datetime import datetime, timedelta
from collections import Counter
from typing import Optional
import re

HINT_SECONDS = 120       # ヒント受付 60秒
VOTE_SECONDS = 60       # 投票 60秒
WOLF_ESCAPE_POINTS = 3
CITIZEN_CORRECT_POINTS = 1

# ====================================お題プリセット=====================================
TOPIC_PAIRS = [
    ("ラーメン", "つけ麺"),("寿司", "刺身"),("カレー", "ハヤシライス"),
    ("ピザ", "カルツォーネ"),("ハンバーガー", "ホットドッグ"),("焼肉", "焼き鳥"),
    ("天ぷら", "フライ"),("たこ焼き", "お好み焼き"),("うどん", "そば"),
    ("コーヒー", "紅茶"),("パン", "ごはん"),("牛乳", "豆乳"),("リンゴ", "ナシ"),
    ("イチゴ", "サクランボ"),("猫", "トラ"),("犬", "オオカミ"),("ペンギン", "アザラシ"),
    ("ゾウ", "サイ"),("キリン", "シマウマ"),("ライオン", "チーター"),("海", "湖"),
    ("山", "丘"),("砂漠", "サバンナ"),("川", "滝"),("雷", "花火"),("虹", "オーロラ"),
    ("雪だるま", "スノーボール"),("新幹線", "特急"),("飛行機", "ヘリコプター"),
    ("自転車", "バイク"),("ロケット", "人工衛星"),("船", "ヨット"),("タクシー", "バス"),
    ("サッカー", "フットサル"),("野球", "ソフトボール"),("バスケ", "3x3"),
    ("テニス", "バドミントン"),("スキー", "スノボ"),("将棋", "チェス"),("本屋", "図書館"),
]

# 許容する「絵文字ベース文字」の範囲
_EMOJI_BASE_RANGES = [
    (0x1F600, 0x1F64F),  # Emoticons
    (0x1F300, 0x1F5FF),  # Misc Symbols & Pictographs
    (0x1F680, 0x1F6FF),  # Transport & Map
    (0x1F1E6, 0x1F1FF),  # Regional Indicator (国旗)
    (0x2600,  0x26FF),   # Misc symbols
    (0x2700,  0x27BF),   # Dingbats
    (0x1F900, 0x1F9FF),  # Supplemental Symbols & Pictographs
    (0x1FA70, 0x1FAFF),  # Symbols & Pictographs Extended-A
    (0x2B00,  0x2BFF),   # Arrows 等
    (0x25A0,  0x25FF),   # Geometric Shapes
]

EMOJI_RE = re.compile(
    r'^[\u2600-\u27BF\u2300-\u23FF\u2B00-\u2BFF'
    r'\U0001F000-\U0001F02F\U0001F0A0-\U0001F0FF'
    r'\U0001F100-\U0001F64F\U0001F680-\U0001F6FF'
    r'\U0001F700-\U0001F77F\U0001F780-\U0001F7FF'
    r'\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF'
    r'\U0001FA00-\U0001FAFF\U0001FB00-\U0001FBFF]+$'
)

# 許容する修飾（数にカウントしない）
_ZWJ = "\u200D"
_VARIATION = "\uFE0F"
_SKIN_TONES = tuple(chr(c) for c in range(0x1F3FB, 0x1F3FF + 1))

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "change-me")  # 本番は環境変数で
)

def _now():
    return datetime.utcnow()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="app/templates")

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/")
def index(req: Request):
    return templates.TemplateResponse("lobby.html", {"request": req})

def _gen_code(n=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

@app.get("/_dev/seed")
def dev_seed():
    code = _gen_code()
    with Session(engine) as session:
        room = Room(code=code, status=GameStatus.lobby)
        session.add(room)
        session.commit()
        host = Player(room_code=code, name="Host", is_host=True)
        session.add(host)
        session.commit()
    return {"room": code, "host": "Host"}

@app.get("/_dev/rooms")
def dev_rooms():
    with Session(engine) as session:
        rows = session.exec(select(Room)).all()
    return {"rooms": [r.code for r in rows], "count": len(rows)}

@app.get("/ping")
def ping():
    return {"pong": True}

@app.get("/_dev/tables")
def list_tables():
    return {"tables": inspect(engine).get_table_names()}

@app.post("/rooms")
def create_room(req: Request, name: str = Form(...)):
    code = _gen_code()
    with Session(engine) as session:
        # 1) Room を作成→確定
        room = Room(code=code, status=GameStatus.lobby)
        session.add(room)
        session.commit()

        # 2) Host を作成→確定
        host = Player(room_code=code, name=name.strip(), is_host=True)
        session.add(host)
        try:
            session.flush()
            host_id = host.id
            session.commit()
        except Exception as e:
            session.rollback()
            print("[create_room] commit error:", repr(e))
            raise HTTPException(status_code=400, detail="名前が重複しています。別名で再試行してください。")
        
        req.session["user_name"] = name.strip()
        req.session["room_code"] = code
        req.session["player_id"] = host_id

        # 303 リダイレクトで /rooms/{code} へ
        return RedirectResponse(url=f"/rooms/{code}", status_code=303)
    
@app.post("/join")
def join(req: Request, code: str = Form(...), name: str = Form(...)):
    code = code.strip().upper()
    with Session(engine) as session:
        room = session.get(Room, code)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
            
        player = Player(room_code=code, name=name.strip(), is_host=False)
        session.add(player)
        try:
            session.flush()
            player_id = player.id
            session.commit()
        except Exception:
            session.rollback()
            raise HTTPException(status_code=400, detail="この部屋に同名の参加者がいます。別名で再試行してください")
    
    # with を出た後は「整数の id」だけを使う（Detached 回避）
    req.session["user_name"] = name.strip()
    req.session["room_code"] = code
    req.session["player_id"] = player_id

    # セッションへ"自分"を保存
    return RedirectResponse(url=f"/rooms/{code}", status_code=303)
    
@app.get("/rooms/{code}")
def room_page(code: str, req: Request):
    with Session(engine) as session:
        room = session.get(Room, code)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        players = session.exec(
            select(Player).where(Player.room_code == code).order_by(Player.id)
        ).all()

        me = _get_me(session, code, req)
        is_host = bool(me and me.is_host and me.room_code == code)
      
    return templates.TemplateResponse("room.html", {
        "request": req,
        "room": room,
        "players": players,
        "is_host":is_host,
    })

@app.get("/rooms/{code}/players")
def room_players_partial(code: str, req: Request):
    with Session(engine) as session:
        room = _get_room_or_404(session, code)
        players = session.exec(
            select(Player).where(Player.room_code == code).order_by(Player.id)
        ).all()
        status = room.status.value if isinstance(room.status, GameStatus) else str(room.status)

    resp = templates.TemplateResponse("_players.html", {
        "request": req,
        "room"   : room,
        "players": players,
    })

    #（任意）ロビー中にフェーズが進んだら自動遷移させたい場合だけ付ける
    if status == "hint":
        resp.headers["HX-Redirect"] = f"/rooms/{code}/hint"
    elif status == "vote":
        resp.headers["HX-Redirect"] = f"/rooms/{code}/vote"
    elif status == "result":
        resp.headers["HX-Redirect"] = f"/rooms/{code}/result"

    return resp


def _get_room_or_404(session: Session, code: str) -> Room:
    room = session.get(Room, code)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room

@app.get("/rooms/{code}/hint")
def hint_page(code: str, req: Request):
    with Session(engine) as session:
        room = _get_room_or_404(session, code)
        # 最新ラウンド取得（今は1ラウンド制なので max(id) でOK）
        rnd = _latest_round(session, code)
        if not rnd:
            # 直接URL叩かれたとき用に救済
            return RedirectResponse(url=f"/rooms/{code}", status_code=303)

        players = session.exec(
            select(Player).where(Player.room_code == code).order_by(Player.id)
        ).all()
        hints = session.exec(
            select(Hint).where(Hint.round_id == rnd.id)
        ).all()
        
        me = _get_me(session, code, req)
        is_host = bool(me and me.is_host and me.room_code == code)

        my_topic = None
        if me:
            my_topic = rnd.spy_topic if (rnd.spy_player_id == me.id) else rnd.topic

    return templates.TemplateResponse("hint.html", {
        "request": req,
        "room": room,
        "round": rnd,
        "players": players,
        "hints": hints,
        "me": me,
        "is_host": is_host,
        "hint_deadline_ms": int(room.hint_deadline.timestamp() * 1000) if room.hint_deadline else 0,
        "my_topic": my_topic,
    })

@app.get("/rooms/{code}/hints")
def hint_list_partial(code: str, req: Request):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        rnd = _latest_round(s, code)
        hints = []
        if rnd:
            hints = s.exec(
                select(Hint).where(Hint.round_id == rnd.id).order_by(Hint.id)
            ).all()
        # ★ status は文字列に確定させてから with を出る
        status = room.status.value if isinstance(room.status, GameStatus) else str(room.status)

    # 部分テンプレを返す
    resp = templates.TemplateResponse("_hints.html", {
        "request": req,
        "room"   : room,  
        "hints": hints,
    })
    # キャッシュ抑止（念のため）
    resp.headers["Cache-Control"] = "no-store"
    # フェーズが進んでいたら自動遷移（任意）
    if status == "vote":
        resp.headers["HX-Redirect"] = f"/rooms/{code}/vote"
    elif status == "result":
        resp.headers["HX-Redirect"] = f"/rooms/{code}/result"
    return resp


def _get_me(session: Session, code: str, req: Request) -> Player | None:
    pid = req.session.get("player_id")
    me = session.get(Player, pid) if pid else None
    if not me:
        # 保険: 名前で一致を試す（同名対策で room_code も条件に）
        name = req.session.get("user_name")
        if name:
            me = session.exec(
                select(Player).where(Player.room_code == code, Player.name == name)
            ).first()
    return me

@app.get("/_dev/whoami")
def whoami(req: Request):
    return dict(req.session)

@app.get("/rooms/{code}/vote")
def vote_page(code: str, req: Request):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        rnd = _latest_round(s, code)
        if not rnd:
            return RedirectResponse(url=f"/rooms/{code}", status_code=303)

        hints = s.exec(select(Hint).where(Hint.round_id == rnd.id)).all()
        players = s.exec(select(Player).where(Player.room_code == code).order_by(Player.id)).all()

        me = _get_me(s, code, req)
        me_id = me.id if me else None
        is_host = bool(me and me.is_host and me.room_code == code)

        # 自分以外を候補にする
        candidates = s.exec(
            select(Player)
            .where(Player.room_code == code, Player.id != me_id)
            .order_by(Player.id)
        ).all()

        # この部屋の参加者なら投票できる（ホストも投票可にするならそのまま）
        can_vote = bool(me and me.room_code == code)

        my_topic = None
        if me:
            my_topic = rnd.spy_topic if (rnd.spy_player_id == me.id) else rnd.topic

    return templates.TemplateResponse("vote.html", {
        "request": req,
        "room": room,
        "round": rnd,
        "players": players,
        "hints": hints,
        "is_host": is_host,
        "me_id": me_id,
        "candidates": candidates,
        "can_vote": can_vote,
        "vote_deadline_ms": int(room.vote_deadline.timestamp() * 1000) if room.vote_deadline else 0,
        "my_topic": my_topic,
    })

    
@app.post("/rooms/{code}/vote")
def submit_vote(code: str, req: Request, target_player_id: int = Form(...)):
    with Session(engine) as session:
        room = _get_room_or_404(session, code)
        rnd = _latest_round(session, code)
        if not rnd:
            raise HTTPException(status_code=400, detail="round not found")
        
        me = _get_me(session, code, req)
        if not me:
            raise HTTPException(status_code=403, detail="not joined")
        
        # 自分以外に投票（自分投票を禁ずる場合）
        if target_player_id == me.id:
            raise HTTPException(status_code=400, detail="cannnot vote for yourself")
        
        target = session.get(Player, target_player_id)
        if not (target and target.room_code == code):
            raise HTTPException(status_code=404, detail="target not found")
        
        # 1ラウンド1票
        existing = session.exec(
            select(Vote).where(Vote.round_id == rnd.id, Vote.voter_id == me.id)
        ).first()
        if existing:
            existing.target_player_id = target_player_id
        else:
            session.add(Vote(round_id=rnd.id, voter_id=me.id, target_player_id=target_player_id))
        session.commit()
    return RedirectResponse(url=f"/rooms/{code}/vote", status_code=303)

@app.post("/rooms/{code}/close_vote")
def close_vote(code: str, req: Request):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        me = _get_me(s, code, req)
        if not (me and me.is_host and me.room_code == code):
            raise HTTPException(status_code=403, detail="Only host can close vote")

        # 二重締切ガード
        if room.status != GameStatus.vote:
            return RedirectResponse(url=f"/rooms/{code}/{room.status.value}", status_code=303)

        rnd = _latest_round(s, code)
        if not rnd:
            raise HTTPException(status_code=400, detail="round not found")
        
        # ワードウルフ集計
        _tally_wordwolf_and_apply_scores(s, rnd)
        room.status = GameStatus.result
        s.commit()

    return RedirectResponse(url=f"/rooms/{code}/result", status_code=303)

@app.get("/rooms/{code}/result")
def result_page(code: str, req: Request):
    with Session(engine) as session:
        room = _get_room_or_404(session, code)
        rnd = _latest_round(session, code)
        players = session.exec(select(Player).where(Player.room_code == code).order_by(Player.score.desc(), Player.id)).all()
        
        hints = []
        if rnd:
            hints = session.exec(
                select(Hint)
                .where(Hint.round_id == rnd.id)
                .order_by(Hint.id)
            ).all()
        hints_by_player = {h.player_id: h for h in hints}

        # 今ラウンドの投票一覧（誰が誰に入れたか）とタリー
        votes = session.exec(
            select(Vote)
            .where(Vote.round_id == (rnd.id if rnd else -1))
        ).all() if rnd else []
        tally = Counter(v.target_player_id for v in votes)

        me = _get_me(session, code, req)
        is_host = bool(me and me.is_host and me.room_code == code)
        spy = session.get(Player, rnd.spy_player_id) if (rnd and rnd.spy_player_id) else None

        # 勝敗判定
        spy_id = rnd.spy_player_id if rnd else None
        correct_voters = {v.voter_id for v in votes if v.target_player_id == spy_id}
        wolf_won = (len(correct_voters) == 0)   # 正解者０ならウルフの勝ち

    return templates.TemplateResponse("result.html", {
        "request": req,
        "room": room,
        "round": rnd,
        "players": players,
        "vote": votes,
        "tally": dict(tally),
        "is_host": is_host,
        "spy": spy,
        "hints_by_player": hints_by_player,
        "wolf_won": wolf_won,
        "correct_voters": list(correct_voters)
    })

def _latest_round(session: Session, code: str) -> Round | None:
    return session.exec(
        select(Round).where(Round.room_code == code).order_by(col(Round.id).desc())
    ).first()

def _players_in_room(session: Session, code: str) -> list[Player]:
    return session.exec(
        select(Player).where(Player.room_code == code).order_by(Player.id)
    ).all()

# def _tally_and_apply_scores(session: Session, round_id: int) -> None:
#     votes = session.exec(select(Vote).where(Vote.round_id == round_id)).all()
#     tally = Counter(v.target_player_id for v in votes)
#     if not tally:
#         return
#     ids = list(tally.keys())
#     players = session.exec(select(Player).where(Player.id.in_(ids))).all()
#     by_id = {p.id: p for p in players}
#     for pid, pts in tally.items():
#         p = by_id.get(pid)
#         if p:
#             p.score += int(pts)

def _maybe_autoadvance(session: Session, room: Room):
    """
    部屋のフェーズを締め切り or 全員完了で自動進行させる。
    HINT_SECONDS / VOTE_SECONDS を使用。
    """
    now = _now()
    rnd = _latest_round(session, room.code)
    if not rnd:
        return
    
    players = _players_in_room(session, room.code)
    num_players = len(players)

    # HINT フェーズ：締め切り or 全員提出で VOTE へ
    if room.status == GameStatus.hint:
        # デッドライン未設定なら設定（保険）
        if not room.hint_deadline:
            room.hint_deadline = now + timedelta(seconds=HINT_SECONDS)
        
        # 提出者数
        submitted = session.exec(
            select(func.count(Hint.id)).where(Hint.round_id == rnd.id)
        ).one()

        # 全員提出（２人以上のとき）
        should_close = (
            (room.hint_deadline and now >= room.hint_deadline) or
            (num_players >= 2 and submitted >= num_players)
        )

        if should_close:
            room.status = GameStatus.vote
            room.vote_deadline = now + timedelta(seconds=VOTE_SECONDS)
            session.commit()
            return
    
    # VOTE フェーズ：締め切り or 全員投票で RESULT へ（集計も実施）
    elif room.status == GameStatus.vote:
        if not room.vote_deadline:
            room.vote_deadline = now + timedelta(seconds=VOTE_SECONDS)
        
        voted = session.exec(
            select(func.count(func.distinct(Vote.voter_id))).where(Vote.round_id == rnd.id)
        ).one()

        # 全員提出（２人以上のとき）
        should_close = (
            (room.vote_deadline and now >= room.vote_deadline) or
            (num_players >= 2 and voted >= num_players)
        )
    
        if should_close:
            # _tally_and_apply_scores(session, rnd.id)
            _tally_wordwolf_and_apply_scores(session, rnd.id)
            room.status = GameStatus.result
            session.commit()
            return

@app.get("/rooms/{code}/phase")
def phase_pulse(code: str, at: str | None = Query(default=None)):
    """
    クライアントの現在のフェーズ（at）と部屋の実フェーズが異なるときだけHX-Redirect を返す。
    呼ばれるたびに _maybe_autoadbance で自動進行も評価。
    """
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        # ★ ここで自動更新を評価
        _maybe_autoadvance(s, room)
        # 再取得（進んでいるかもしれない）
        s.refresh(room)
        status = room.status.value if isinstance(room.status, GameStatus) else str(room.status)
    
    if at and at == status:
        return Response(status_code=204)
    targets = {
        "lobby"  : f"/rooms/{code}",
        "hint"   : f"/rooms/{code}/hint",
        "vote"   : f"/rooms/{code}/vote",
        "result" : f"/rooms/{code}/result"
    }
    resp = Response(status_code=204)
    resp.headers["HX-Redirect"] = targets.get(status, f"/rooms/{code}")
    return resp

@app.post("/rooms/{code}/start")
def start_game(code: str, req: Request):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        me = _get_me(s, code, req)
        if not (me and me.is_host and me.room_code == code):
            raise HTTPException(status_code=403, detail="Only host can start the game")
        
        rnd = _start_wordwolf_round(s, code)
        room.status = GameStatus.hint
        room.hint_deadline = _now() + timedelta(seconds=HINT_SECONDS)
        room.vote_deadline = None
        s.commit()
    return RedirectResponse(url=f"/rooms/{code}/hint", status_code=303)

@app.post("/rooms/{code}/next_rounds")
def next_round(code: str, req: Request):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        me = _get_me(s, code, req)
        if not (me and me.is_host and me.room_code == code):
            raise HTTPException(status_code=403, detail="Only host can start the game")
        
        rnd = _start_wordwolf_round(s, code)
        room.round = (room.round or 0) + 1
        room.status = GameStatus.hint
        room.hint_deadline = _now() + timedelta(seconds=HINT_SECONDS)
        room.vote_deadline = None
        s.commit()
    return RedirectResponse(url=f"/rooms/{code}/hint", status_code=303)

@app.post("/rooms/{code}/lock_hints")
def lock_hints(code: str, req: Request):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        me = _get_me(s, code, req)
        if not (me and me.is_host and me.room_code == code):
            raise HTTPException(status_code=403, detail="Only host can start the game")
    
        if room.status != GameStatus.hint:
            return RedirectResponse(url=f"/rooms/{code}/{room.status.value}", status_code=303)
    
        # 投票フェーズへ
        room.status = GameStatus.vote
        # 自動締め切りを使っている場合はここで期限もセット
        room.vote_deadline = _now() + timedelta(seconds=VOTE_SECONDS)
        s.commit()
    
    # htmx経由ならHX-Redirect、通常フォームなら303
    if req.headers.get("HX-Request") == "true":
        return Response(status_code=204, headers={"HX-Redirect": f"/rooms/{code}/vote"})
    
    return RedirectResponse(url=f"/rooms/{code}/vote", status_code=303)

@app.get("/rooms/{code}/timeleft")
def timeleft(code: str, phase: str = Query(...)):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        now = _now()
        if phase == "hint":
            dl = room.hint_deadline
        elif phase == "vote":
            dl = room.vote_deadline
        else:
            raise HTTPException(status_code=400, detail="unknown phase")
    
    secs = max(0, ceil((dl - now).total_seconds())) if dl else 0
    return HTMLResponse(f'<span id="nountdown" data-phase="{phase}">{secs}</span>')

def _phase_and_remaining(room: Room) -> tuple[str, int]:
    now = _now()
    if room.status == GameStatus.hint:
        dl = room.hint_deadline
    elif room.status == GameStatus.vote:
        dl = room.vote_deadline
    else:
        dl = None
    remaining = max(0, int((dl - now).total_seconds())) if dl else 0
    status = room.status.value if hasattr(room.status, "value") else str(room.status)
    return status, remaining

@app.get("/rooms/{code}/clock")
def clock_partial(code: str, req: Request):
    with Session(engine) as s:
        room = _get_room_or_404(s, code)
        if room.status == GameStatus.hint:
            deadline = room.hint_deadline
        elif room.status == GameStatus.vote:
            deadline = room.vote_deadline
        else:
            deadline = None
    
    remain = max(0, int((deadline - _now()).total_seconds())) if deadline else 0
    return templates.TemplateResponse("_clock.html",{
        "request": req,
        "remain": remain
    })

def _is_emoji_base(ch: str) -> bool:
    cp = ord(ch)
    for a, b in _EMOJI_BASE_RANGES:
        if a <= cp <= b:
            return True
    return False

def _is_emoji_allowed_char(ch: str) -> bool:
    # ベース or 修飾（ZWJ/VS-16/肌色）
    return _is_emoji_base(ch) or ch == _ZWJ or ch == _VARIATION or ch in _SKIN_TONES

def validate_emoji_payload(raw: str) -> str:
    """
    入力が「絵文字のみ、かつ最大3個」であることを検証。
    返り値は保存用の正規化文字列（前後空白除去のみ）。
    NG の場合は ValueError を投げる。
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("絵文字を入力してください。")

    # すべて許容文字か？
    if any(not _is_emoji_allowed_char(ch) for ch in s):
        raise ValueError("絵文字のみで入力してください（文字や数字は不可）。")

    # ベース絵文字（修飾を除いた“個数”）を数える
    base_count = sum(1 for ch in s if _is_emoji_base(ch))
    if base_count == 0:
        raise ValueError("絵文字を入力してください。")
    if base_count > 3:
        raise ValueError("絵文字は最大3つまでです。")

    # （簡易）空白や改行は既に除去、保存はそのまま
    return s

def _start_wordwolf_round(session, code: str) -> Round:
    players = session.exec(
        select(Player).where(Player.room_code == code).order_by(Player.id)
    ).all()
    if len(players) < 2:
        raise HTTPException(status_code=400, detail="プレイヤーが足りません")
    
    spy = random.choice(players)
    
    # 似たお題ペアを選び、どっちを多数派にするかランダムで入れ替え
    base, alt = random.choice(TOPIC_PAIRS)
    if random.random() < 0.5:
        topic_main, spy_topic = base, alt
    else:
        topic_main, spy_topic = alt, base
    
    rnd = Round(
        room_code=code,
        topic=topic_main,
        spy_topic=spy_topic,
        spy_player_id=spy.id,
    )

    session.add(rnd)
    session.commit()
    session.refresh(rnd)
    return rnd

def _is_emoji_only(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    # 英数字などが混ざっていたら却下
    if any(ch.isalnum() for ch in s):
        return False
    # 主な絵文字ブロック以外が含まれていたら却下（簡易）
    return bool(EMOJI_RE.match(s))

def _approx_emoji_count(s: str) -> int:
    # 簡易カウント（合成は大目に数える可能性あり）
    return sum(1 for ch in s if not ch.isspace())

@app.post("/rooms/{code}/hint")
def submit_hint(code: str, req: Request, emoji: str = Form(...), name: Optional[str] = Form(None)):
    emoji = (emoji or "").strip()
    if not emoji:
        raise HTTPException(status_code=400, detail="emoji is required")
    
    # ★ バリデーション：絵文字のみ＆最大3つ（簡易）
    if not _is_emoji_only(emoji):
        raise HTTPException(status_code=400, detail="絵文字のみで入力してください")
    if _approx_emoji_count(emoji) > 3:
        raise HTTPException(status_code=400, detail="絵文字は最大3つまでです")
    
    with Session(engine) as session:
        room = _get_room_or_404(session, code)
        rnd = _latest_round(session, code)
        if not rnd:
            raise HTTPException(status_code=400, detail="round not found")
        
        me = _get_me(session, code, req)
        if me:
            player = me
        else:
            nm = (name or "").strip()
            if not nm:
                raise HTTPException(status_code=401, detail="Please join the room first")
            player = session.exec(
                select(Player).where(Player.room_code == code, Player.name == nm)
            ).first()
            if not player:
                player = Player(room_code=code, name=nm, is_host=False)
                session.add(player)
                session.commit()
                req.session["user_name"] = player.name
                req.session["room_code"] = code
                req.session["player_id"] = player.id
        
        # 同一ラウンド・他人のヒント重複禁止
        dup = session.exec(
            select(Hint).where(
                Hint.round_id == rnd.id,
                Hint.content_emoji == emoji,
                Hint.player_id != player.id
            )
        ).first()
        if dup:
            raise HTTPException(status_code=400, detail="その絵文字セットはすでに使われています。")
        
        existing = session.exec(
            select(Hint).where(Hint.round_id == rnd.id, Hint.player_id == player.id)
        ).first()
        if existing:
            existing.content_emoji = emoji
        else:
            session.add(Hint(round_id=rnd.id, player_id=player.id, content_emoji=emoji))
        session.commit()
    
    if req.headers.get("HX-Request") == "true":
        return Response(status_code=204, header={"HX-Redirect": f"/rooms/{code}/hint"})
    return RedirectResponse(url=f"/rooms/{code}/hint", status_code=303)

def _tally_wordwolf_and_apply_scores(session: Session, rnd: Round):
    votes = session.exec(select(Vote).where(Vote.round_id == rnd.id)).all()
    if not votes:
        if rnd.spy_player_id:
            spy = session.get(Player, rnd.spy_player_id)
            if spy:
                spy.score += WOLF_ESCAPE_POINTS
        return
    
    wolves = {rnd.spy_player_id}    # TODO: 複数狼化したら Assignment で置換
    correct_voters = {v.voter_id for v in votes if v.target_player_id in wolves}

    if correct_voters:
        for p in session.exec(select(Player).where(Player.id.in_(correct_voters))):
            p.score += CITIZEN_CORRECT_POINTS
    
    else:
        # 誰も当てられない　→　各狼　+3 （現状は単狼=spy のみ）
        for p in session.exec(select(Player).where(Player.id.in_(wolves))):
            p.score += WOLF_ESCAPE_POINTS
    
    spy_id = rnd.spy_player_id
