import sqlite3
import abc
from .base import Database

from logzero import logger

class SqliteDatabase(Database):

    def initialise(self, settings):
        logger.info("sqlite_database: initialise()")
        con = None

        create = False

        self.db_name = settings["db_name"]

        try:
            con = sqlite3.connect(self.db_name)
            cur = con.cursor()
            cur.execute("SELECT * FROM active")
            _ = cur.fetchone()
        except sqlite3.Error:
            # no active table
            create = True
        finally:
            if con:
                con.close()

        if create:
            self.create_schema()
        else:
            logger.info("sqlite_database: schema ready")


    def create_schema(self):
        logger.debug("sqlite_database: create_schema()")
        con = None

        try:
            con = sqlite3.connect(self.db_name)
            cur = con.cursor()
            cur.execute("CREATE TABLE active (environment_group TEXT, environment TEXT, endpoint_group TEXT, endpoint TEXT, timestamp INTEGER, message TEXT, url TEXT)")
            con.commit()
        except sqlite3.Error as e:
            logger.error("sqlite_database: problem during create_schema() - %s" % str(e))
        finally:
            if con:
                con.close()

    def get_active(self, incident):
        logger.debug("sqlite_database: get_active()")
        con = None
        try:
            con = sqlite3.connect(self.db_name)
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute('''SELECT * FROM active WHERE environment_group = ? AND environment = ? AND endpoint_group = ? AND endpoint = ?''',
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint))
            data = cur.fetchone()
            return data
        except sqlite3.Error as e:
            logger.error("sqlite_database: problem during active_exists() - %s" % str(e))
        finally:
            if con:
                con.close()

    def get_all_actives(self):
        logger.debug("sqlite_database: get_all_actives()")
        con = None
        try:
            con = sqlite3.connect(self.db_name)
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("SELECT * FROM active")
            data = cur.fetchall()
            return data
        except sqlite3.Error as e:
            logger.error("sqlite_database: problem during get_active_alerts() - %s" % str(e))
            return None
        finally:
            if con:
                con.close()

    def active_exists(self, incident):
        logger.debug("sqlite_database: active_exists()")
        con = None
        try:
            con = sqlite3.connect(self.db_name)
            cur = con.cursor()
            cur.execute('''SELECT COUNT(*) FROM active WHERE environment_group = ? AND environment = ? AND endpoint_group = ? AND endpoint = ?''',
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint))
            data = cur.fetchone()
            return int(data[0]) > 0
        except sqlite3.Error as e:
            logger.error("sqlite_database: problem during active_exists() - %s" % str(e))
        finally:
            if con:
                con.close()

    def save_active(self, incident):
        logger.debug("sqlite_database: save_active()")
        con = None
        try:
            con = sqlite3.connect(self.db_name)
            cur = con.cursor()
            cur.execute("INSERT INTO active VALUES (?,?,?,?,?,?,?)",
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint, incident.timestamp, incident.message, incident.endpoint.url))
            con.commit()
        except sqlite3.Error as e:
            logger.error("sqlite_database: problem during save_active() - %s" % str(e))
        finally:
            if con:
                con.close()

    def remove_active(self, incident):
        logger.debug("sqlite_database: remove_active()")
        con = None
        try:
            con = sqlite3.connect(self.db_name)
            cur = con.cursor()
            cur.execute("DELETE FROM active WHERE environment_group = ? AND environment = ? AND endpoint_group = ? AND endpoint = ?",
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint))
            con.commit()
        except sqlite3.Error as e:
            logger.error("sqlite_database: problem during remove_active() - %s" % str(e))
        finally:
            if con:
                con.close()
