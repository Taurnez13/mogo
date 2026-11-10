"""Seed (or re-seed) the database from an ONIX file:  python seed.py path/to/onix.xml  (or use ONIX import in the UI)"""
import sys
from app.db import init_db, SessionLocal
from app.onix.importer import OnixImporter

init_db()
db = SessionLocal()
stats = OnixImporter(db).import_file(sys.argv[1], progress=lambda n: print(f"  {n} products…"))
print(stats)
for e in stats.errors[:10]:
    print("  ", e)
