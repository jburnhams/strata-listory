"""Small persisted read models and local asset locations. No source inspection on reads."""
import json
from datetime import datetime, timezone
from app import db


def save(kind,owner,value):
    c=db.connect()
    try:
        with c:c.execute('INSERT OR REPLACE INTO read_models(kind,owner,data,updated) VALUES (?,?,?,?)',(kind,str(owner),json.dumps(value,allow_nan=False),datetime.now(timezone.utc).isoformat()))
    finally:c.close()


def get(kind,owner,default=None):
    c=db.connect()
    try:row=c.execute('SELECT data FROM read_models WHERE kind=? AND owner=?',(kind,str(owner))).fetchone()
    finally:c.close()
    return json.loads(row[0]) if row else default


def owners(kind):
    c=db.connect()
    try:return [r[0] for r in c.execute('SELECT owner FROM read_models WHERE kind=?',(kind,))]
    finally:c.close()


def many(kind,owners):
    ids=[str(owner) for owner in owners]
    if not ids:return {}
    c=db.connect()
    try:return {r['owner']:json.loads(r['data']) for r in c.execute(f"SELECT owner,data FROM read_models WHERE kind=? AND owner IN ({','.join('?' for _ in ids)})",[kind]+ids)}
    finally:c.close()
