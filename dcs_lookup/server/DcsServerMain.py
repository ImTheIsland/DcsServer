import json
from pathlib import Path

from fastapi import FastAPI


from dcs_lookup.server.routers import dcs, keywords, vendors
from dcs_lookup.db.database import Base, engine

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"

Base.metadata.create_all(bind=engine)

app = FastAPI(title="DCS Lookup", version="0.2.0")

with open(_CONFIG_PATH) as f:
    app.state.config = json.load(f)

app.include_router(dcs.router)
app.include_router(vendors.router)
app.include_router(keywords.router)
