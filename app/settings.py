import os
import database
from database.sqlite_database import SqliteDatabase

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS"))
ENDPOINT_DEFINITIONS_FILE = os.getenv("ENDPOINT_DEFINITIONS_FILE")
ALERT_DEFINITIONS_FILE = os.getenv("ALERT_DEFINITIONS_FILE")
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT_SECONDS"))


def get_database():
  db = SqliteDatabase()
  db.initialise({
    "db_name": os.getenv("DB_NAME")
  })

  return db
