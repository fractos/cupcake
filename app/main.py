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
import signal
import os
import boto3
from models import Incident
import settings

requested_to_quit = False
last_summary_emitted = 0


def main():
    logger.info("starting...")

    setup_signal_handling()

    db = settings.get_database()

    while lifecycle_continues():
        lifecycle(db)

        if lifecycle_continues():
            logger.info("sleeping for %s seconds..." % settings.SLEEP_SECONDS)
            for _ in range(settings.SLEEP_SECONDS):
                if lifecycle_continues():
                    time.sleep(1)


def lifecycle_continues():
    return not requested_to_quit


def signal_handler(signum, frame):
    logger.info("Caught signal %s" % signum)
    global requested_to_quit
    requested_to_quit = True


def setup_signal_handling():
    logger.info("setting up signal handling")
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def lifecycle(db):
    global last_summary_emitted

    endpoint_definitions = json.loads(
        open(settings.ENDPOINT_DEFINITIONS_FILE).read()
    )

    alert_definitions = json.loads(
        open(settings.ALERT_DEFINITIONS_FILE).read()
    )

    if settings.SUMMARY_ENABLED:
        seconds=time.time()-last_summary_emitted
        if seconds >= settings.SUMMARY_SLEEP_SECONDS:
            last_summary_emitted = time.time()
            emit_summary(endpoint_definitions, alert_definitions, db)

    endpoints_check(endpoint_definitions, alert_definitions, db)


def emit_summary(endpoints, alerts, db):
    """
    Show a summary via a subset of notification types
    """
    logger.info('emit summary')

    number_of_endpoints = 0

    for group in endpoints['groups']:
        for environment in group['environments']:
            for endpoint_group in environment['endpoint-groups']:
                if endpoint_group['enabled'] == "true":
                    for _ in endpoint_group['endpoints']:
                        number_of_endpoints = number_of_endpoints + 1

    message = 'Cupcake is alive and currently monitoring %d endpoints.' % number_of_endpoints

    actives = db.get_all_actives()
    actives_message = ''

    for active in actives:
        actives_message = actives_message + active['message'] + '\n'

    if len(actives) == 0:
        message = message + '\n\n\nCupcake is not currently aware of any alerts.'
    else:
        message = message + '\n\n\nCupcake is aware of the following alerts:\n%s' % actives_message

    incident = Incident(
        timestamp=time.time(),
        environment_group='',
        environment='',
        endpoint_group='',
        endpoint='',
        result={},
        url='',
        expected='',
        message=message
    )

    for alert in alerts['alerts']:
        if alert['id'] in settings.SUMMARY_NOTIFICATION_LIST:
            if alert['@type'] == "alert-slack":
                alert_slack(incident, alert)


def endpoints_check(endpoints, alerts, db):
    logger.info('collecting endpoint health')

    for group in endpoints['groups']:
        environment_group_id = group['id']

        for environment in group['environments']:
            environment_id = environment['id']

            for endpoint_group in environment['endpoint-groups']:
                endpoint_group_name = endpoint_group['id']
                endpoint_group_enabled = endpoint_group['enabled']

                if endpoint_group_enabled == "true":
                    for endpoint in endpoint_group['endpoints']:
                        endpoint_name = endpoint['id']
                        endpoint_url = endpoint['url']

                        endpoint_expected = ''
                        if 'expected' in endpoint:
                            endpoint_expected = endpoint['expected']

                        result = test_endpoint(url=endpoint_url, expected=endpoint_expected)

                        incident = Incident(
                            timestamp=datetime.now(timezone.utc).astimezone().isoformat(),
                            environment_group=environment_group_id,
                            environment=environment_id,
                            endpoint_group=endpoint_group_name,
                            endpoint=endpoint_name,
                            result=result,
                            url=endpoint_url,
                            expected=endpoint_expected
                        )

                        handle_result(
                            incident,
                            alerts=alerts,
                            db=db
                        )

                        if not lifecycle_continues():
                            return


def test_endpoint(url, expected):
    logger.info('testing endpoint ' + url)
    if not lifecycle_continues():
        logger.info('test_endpoint: bailing')
        return False

    parse_result = urlparse(url)

    start_time = time.time()

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
                    "time": relativedelta(seconds=time.time()-start_time),
                    "message": "OK"
                }
            else:
                return {
                    "result": False,
                    "time": relativedelta(seconds=time.time()-start_time),
                    "actual": status,
                    "message": "BAD"
                }

        except Exception:
            pass

        return {
            "result": False,
            "time": relativedelta(seconds=time.time()-start_time),
            "message": "TIMEOUT"
        }


    elif parse_result.scheme == 'tcp':
        s = socket.socket()
        try:
            s.settimeout(settings.CONNECTION_TIMEOUT)
            s.connect((parse_result.hostname, parse_result.port))
        except socket.timeout:
            logger.info(
                "tcp endpoint %s hit timeout" % parse_result.netloc
            )
            return {
                "result": False,
                "time": relativedelta(seconds=time.time()-start_time),
                "message": "TIMEOUT"
            }
        except Exception as e:
            logger.info(
                "tcp endpoint %s had a problem: %s" % (parse_result.netloc, e)
            )
            return {
                "result": False,
                "time": relativedelta(seconds=time.time()-start_time),
                "message": "BAD"
            }
        finally:
            s.close()
        return {
            "result": True,
            "time": relativedelta(seconds=time.time()-start_time)
        }


def handle_result(incident, alerts, db, url="none"):
    if not lifecycle_continues():
        logger.info('handle_result: bailing')
        return

    attrs = ['years', 'months', 'days', 'hours', 'minutes', 'seconds']
    human_readable = lambda delta: ['%d %s' % (getattr(delta, attr), getattr(delta, attr) > 1 and attr or attr[:-1])
        for attr in attrs if getattr(delta, attr)]

    logger.info('result: timestamp: %s, environment_group: %s environment: %s, endpoint_group: %s, endpoint: %s, result: %s, url: %s, expected: %s, time: %s'
        % (incident.timestamp, incident.environment_group, incident.environment, incident.endpoint_group, incident.endpoint, incident.result['result'], incident.url, incident.expected, ", ".join(human_readable(incident.result['time']))))

    if 'actual' in incident.result:
        logger.info('actual: %s' % incident.result['actual'])

    if db.active_exists(incident):
        # there's an existing alert for this tuple
        active = db.get_active(incident)
        if incident.result["result"]:
            # existing alert cleared
            logger.info("cleared alert")

            delta = relativedelta(seconds=time.time()-active["timestamp"])
            incident.message = '%s %s %s %s now OK after %s' % \
                (incident.environment_group, incident.environment, incident.endpoint_group, incident.endpoint,
                    ", ".join(human_readable(delta)))

            db.remove_active(incident)

            process_alerts(incident, alerts)
        else:
            # existing alert continues
            logger.info("alert continues")
            pass

    else:
        # no existing alert for this tuple
        if incident.result["result"]:
            # result was good
            logger.info("no alert")
            pass
        else:
            # result was bad
            logger.info("new alert recorded")
            incident.timestamp = time.time()
            incident.presentation_message = "result was %s" % incident.result["message"]
            incident.message = '%s %s %s %s expected %s' % (incident.environment_group, incident.environment, incident.endpoint_group, incident.endpoint, incident.expected)

            if 'actual' in incident.result:
                incident.presentation_message = incident.presentation_message + ", actual: %s" % incident.result["actual"]
                incident.message = incident.message + ", actual: %s" % incident.result["actual"]
            else:
                incident.message = incident.message + ", actual: %s" % incident.result["message"]

            db.save_active(incident)

            process_alerts(incident, alerts)


def process_alerts(incident, alert_definitions):
    logger.info('processing alerts')

    for alert in alert_definitions['alerts']:

        type = alert['@type']
        if type == "alert-slack":
            alert_slack(incident, alert)
        elif type == "alert-sns":
            alert_sns(incident, alert)


def alert_slack(incident, alert):
    logger.info('alert_slack: %s' % incident.message)
    _ = requests.post(alert['url'], json={"text": incident.message, "link_names": 1})


def alert_sns(incident, alert):
    logger.info('alert_sns: %s' % incident.message)

    sns_client = boto3.client('sns', alert['region'])

    _ = sns_client.publish(
        TopicArn=alert['arn'],
        Message=json.dumps(incident.as_dict(), indent=4, sort_keys=True)
    )


if __name__ == "__main__":
    main()
