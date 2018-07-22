from logzero import logger
from urllib.parse import urlparse
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
import re
import socket
import http.client
import json
import time
import requests
import sqlite3
import settings

def main():
    db = settings.get_database()
    logger.info("starting...")

    while True:
        lifecycle(db)
        time.sleep(settings.SLEEP_SECONDS)


def lifecycle(db):
    endpoint_definitions = json.loads(
        open(settings.ENDPOINT_DEFINITIONS_FILE).read()
    )

    alert_definitions = json.loads(
        open(settings.ALERT_DEFINITIONS_FILE).read()
    )

    endpoints_check(endpoint_definitions, alert_definitions, db)


def endpoints_check(endpoints, alerts, db):
    logger.info('collecting endpoint health')

    for group in endpoints['groups']:
        environment_group_id = group['id']

        for environment in group['environments']:
            environment_id = environment['id']

            for endpoint_group in environment['endpoint-groups']:
                endpoint_group_name = endpoint_group['name']
                endpoint_group_enabled = endpoint_group['enabled']

                if endpoint_group_enabled == "true":
                    for endpoint in endpoint_group['endpoints']:
                        endpoint_name = endpoint['name']
                        endpoint_url = endpoint['url']

                        endpoint_expected = ''
                        if 'expected' in endpoint:
                            endpoint_expected = endpoint['expected']

                        # test the endpoint here

                        handle_result(
                            environment_group=environment_group_id,
                            environment=environment_id,
                            endpoint_group=endpoint_group_name,
                            endpoint=endpoint_name,
                            result=test_endpoint(url=endpoint_url, expected=endpoint_expected),
                            url=endpoint_url,
                            expected=endpoint_expected,
                            alerts=alerts,
                            db=db
                        )


def test_endpoint(url, expected):
    logger.info('testing endpoint ' + url)
    parse_result = urlparse(url)

    if parse_result.scheme == 'http' or parse_result.scheme == 'https':
        try:
            conn = None

            if parse_result.scheme == 'http':
                conn = http.client.HTTPConnection(
                    host=parse_result.netloc,
                    timeout=settings.CONNECTION_TIMEOUT)
            else:
                conn = http.client.HTTPSConnection(
                    host=parse_result.netloc,
                    timeout=settings.CONNECTION_TIMEOUT)

            conn.request('GET', parse_result.path)
            status = str(conn.getresponse().status)
            logger.debug('status: %s, expected: %s' % (status, expected))
            if re.match(expected, status):
                return {
                    "result": True,
                    "message": "OK"
                }
            else:
                return {
                    "result": False,
                    "actual": status,
                    "message": "BAD"
                }

        except Exception:
            pass

        return {
            "result": False,
            "message": "OK"
        }


    elif parse_result.scheme == 'tcp':
        s = socket.socket()
        try:
            s.settimeout(settings.CONNECTION_TIMEOUT)
            s.connect((parse_result.hostname, parse_result.port))
        except Exception as e:
            logger.info(
                "tcp endpoint %s had a problem: %s" % (parse_result.netloc, e)
            )
            return {
                "result": False
            }
        finally:
            s.close()
        return {
            "result": True
        }


def handle_result(environment_group, environment, endpoint_group, endpoint, result, expected, alerts, db, url="none"):
    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    logger.info('result: timestamp: %s, environment_group: %s environment: %s, endpoint_group: %s, endpoint: %s, result: %s, url: %s, expected: %s'
        % (timestamp, environment_group, environment, endpoint_group, endpoint, result['result'], url, expected))
    if 'actual' in result:
        logger.info('actual: %s' % result['actual'])

    attrs = ['years', 'months', 'days', 'hours', 'minutes', 'seconds']
    human_readable = lambda delta: ['%d %s' % (getattr(delta, attr), getattr(delta, attr) > 1 and attr or attr[:-1])
        for attr in attrs if getattr(delta, attr)]

    if db.active_exists(environment_group, environment, endpoint_group, endpoint):
        # there's an existing alert for this tuple
        active = db.get_active(environment_group, environment, endpoint_group, endpoint)
        if result["result"]:
            # existing alert cleared
            logger.info("cleared alert")

            delta = relativedelta(seconds=time.time()-active["timestamp"])
            message = '%s %s %s %s now OK after %s' % \
                (environment_group, environment, endpoint_group, endpoint,
                    ", ".join(human_readable(delta)))

            db.remove_active(environment_group, environment, endpoint_group, endpoint)

            process_alerts(message, alerts)
        else:
            # existing alert continues
            logger.info("alert continues")
            pass

    else:
        # no existing alert for this tuple
        if result["result"]:
            # result was good
            logger.info("no alert")
            pass
        else:
            # result was bad
            logger.info("new alert recorded")
            timestamp = time.time()
            presentation_message = "result was %s" % result["message"]
            message = '%s %s %s %s expected %s' % (environment_group, environment, endpoint_group, endpoint, expected)

            if 'actual' in result:
                presentation_message = presentation_message + ", actual: %s" % result["actual"]
                message = message + ", actual: %s" % result["actual"]

            db.save_active(environment_group, environment, endpoint_group, endpoint, timestamp, presentation_message)

            process_alerts(message, alerts)


def process_alerts(message, alert_definitions):
    logger.info('processing alerts')

    for alert in alert_definitions['alerts']:

        type = alert['@type']
        if type == "alert-slack":
            alert_slack(message, alert)
        elif type == "alert-sns":
            alert_sns(message, alert)


def alert_slack(message, alert):
    logger.info('alert_slack: %s' % message)
    r = requests.post(alert['url'], json={"text": message, "link_names": 1})
    pass


def alert_sns(message, alert):
    logger.info('alert_sns: %s' % message)
    pass


if __name__ == "__main__":
    main()
