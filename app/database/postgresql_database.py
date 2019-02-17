import psycopg2
import psycopg2.extras
import abc
from .base import Database
from logzero import logger

class PostgreSqlDatabase(Database):

    def initialise(self, settings):
        logger.info("postgresql_database: initialise()")
        con = None

        create = False

        self.connection_string = "dbname='%s' user='%s' host='%s' password='%s'" % \
            (settings["dbname"], settings["user"], settings["host"], settings["password"])

        try:
            con = psycopg2.connect(self.connection_string)
            cur = con.cursor()
            cur.execute("SELECT * FROM active")
        except psycopg2.Error:
            # no active table
            create = True
        finally:
            if con:
                con.close()

        if create:
            self.create_schema()
        else:
            logger.info("postgresql_database: schema ready")


    def create_schema(self):
        logger.debug("postgresql_database: create_schema()")
        con = None

        try:
            con = psycopg2.connect(self.connection_string)
            cur = con.cursor()
            cur.execute("CREATE TABLE active (environment_group CHARACTER VARYING(500) NOT NULL, environment CHARACTER VARYING(500) NOT NULL, endpoint_group CHARACTER VARYING(500) NOT NULL, endpoint CHARACTER VARYING(500) NOT NULL, timestamp INTEGER NOT NULL, message CHARACTER VARYING(500) NOT NULL, url CHARACTER VARYING(500) NOT NULL)")
            con.commit()
        except psycopg2.Error as e:
            logger.error("postgresql_database: problem during create_schema() - %s" % str(e))
        finally:
            if con:
                con.close()

    def get_active(self, incident):
        logger.debug("postgresql_database: get_active()")
        con = None
        try:
            con = psycopg2.connect(self.connection_string)
            cur = con.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute('''SELECT * FROM active WHERE environment_group = %s AND environment = %s AND endpoint_group = %s AND endpoint = %s''',
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint))
            data = cur.fetchone()
            return data
        except psycopg2.Error as e:
            logger.error("postgresql_database: problem during get_active() - %s" % str(e))
        finally:
            if con:
                con.close()

    def get_all_actives(self):
        logger.debug("postgresql_database: get_all_actives()")
        con = None
        try:
            con = psycopg2.connect(self.connection_string)
            cur = con.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT * FROM active")
            data = cur.fetchall()
            return data
        except psycopg2.Error as e:
            logger.error("postgresql_database: problem during get_all_actives() - %s" % str(e))
            return None
        finally:
            if con:
                con.close()

    def active_exists(self, incident):
        logger.debug("postgresql_database: active_exists()")
        con = None
        try:
            con = psycopg2.connect(self.connection_string)
            cur = con.cursor()
            cur.execute('''SELECT COUNT(*) FROM active WHERE environment_group = %s AND environment = %s AND endpoint_group = %s AND endpoint = %s''',
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint))
            data = cur.fetchone()
            return int(data[0]) > 0
        except psycopg2.Error as e:
            logger.error("postgresql_database: problem during active_exists() - %s" % str(e))
        finally:
            if con:
                con.close()

    def save_active(self, incident):
        logger.debug("postgresql_database: save_active()")
        con = None
        try:
            con = psycopg2.connect(self.connection_string)
            cur = con.cursor()
            cur.execute("INSERT INTO active VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint, incident.timestamp, incident.message, incident.endpoint.url))
            con.commit()
        except psycopg2.Error as e:
            logger.error("postgresql_database: problem during save_active() - %s" % str(e))
        finally:
            if con:
                con.close()

    def remove_active(self, incident):
        logger.debug("postgresql_database: remove_active()")
        con = None
        try:
            con = psycopg2.connect(self.connection_string)
            cur = con.cursor()
            cur.execute("DELETE FROM active WHERE environment_group = %s AND environment = %s AND endpoint_group = %s AND endpoint = %s",
                (incident.endpoint.environment_group, incident.endpoint.environment, incident.endpoint.endpoint_group, incident.endpoint.endpoint))
            con.commit()
        except psycopg2.Error as e:
            logger.error("postgresql_database: problem during remove_active() - %s" % str(e))
        finally:
            if con:
                con.close()
