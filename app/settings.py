import os
import database
import re
import distutils.util
from database.sqlite_database import SqliteDatabase
from database.postgresql_database import PostgreSqlDatabase

DEBUG = bool(distutils.util.strtobool(os.getenv("DEBUG", "False")))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS"))
ENDPOINT_DEFINITIONS_FILE = os.getenv("ENDPOINT_DEFINITIONS_FILE")
ALERT_DEFINITIONS_FILE = os.getenv("ALERT_DEFINITIONS_FILE")
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT_SECONDS"))
DB_TYPE = os.getenv("DB_TYPE")
SUMMARY_ENABLED = bool(distutils.util.strtobool(os.getenv("SUMMARY_ENABLED")))
SUMMARY_SLEEP_SECONDS = int(os.getenv("SUMMARY_SLEEP_SECONDS"))
METRICS_DEFINITIONS_FILE = os.getenv("METRICS_DEFINITIONS_FILE")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", default="2"))
SHOW_BODY_IN_DEBUG_ON_UNEXPECTED_STATUS = bool(distutils.util.strtobool(os.getenv("SHOW_BODY_IN_DEBUG_ON_UNEXPECTED_STATUS", "False")))
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

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
