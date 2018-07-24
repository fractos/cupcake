import os
import database
from database.sqlite_database import SqliteDatabase
from database.postgresql_database import PostgreSqlDatabase

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS"))
ENDPOINT_DEFINITIONS_FILE = os.getenv("ENDPOINT_DEFINITIONS_FILE")
ALERT_DEFINITIONS_FILE = os.getenv("ALERT_DEFINITIONS_FILE")
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT_SECONDS"))
DB_TYPE = os.getenv("DB_TYPE")


def get_database():
  if DB_TYPE == "postgresql":
    return get_database_postgresql()
  else:
    return get_database_sqlite()


def get_database_postgresql():
  db = PostgreSqlDatabase()
  db.initialise({
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "host": os.getenv("DB_HOST"),
    "password": os.getenv("DB_PASSWORD")
  })

  return db


def get_database_sqlite():
  db = SqliteDatabase()
  db.initialise({
    "db_name": os.getenv("DB_NAME")
  })

  return db
