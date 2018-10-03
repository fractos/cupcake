from logzero import logger
import logging
import logzero
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
from models import Incident, Threshold
from alerts import deliver_alert_to_groups, deliver_alert_to_group, get_alerts_in_group
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


def emit_summary(endpoints, alert_definitions, db):
    """
    Show a summary via a subset of notification types
    """
    logger.info("emit summary")

    number_of_endpoints = 0

    for group in endpoints["groups"]:
        for environment in group["environments"]:
            for endpoint_group in environment["endpoint-groups"]:
                if endpoint_group["enabled"] == "true":
                    for _ in endpoint_group["endpoints"]:
                        number_of_endpoints = number_of_endpoints + 1

    message = "Cupcake is alive and currently monitoring {} endpoints.".format(number_of_endpoints)

    actives = db.get_all_actives()
    actives_message = ""

    for active in actives:
        actives_message = actives_message + "{}\n".format(active["message"])

    if len(actives) == 0:
        message = message + "\n\nCupcake is not currently aware of any alerts."
    else:
        message = message + "\n\nCupcake is aware of the following alerts:\n%s" % actives_message

    incident = Incident(
        timestamp=time.time(),
        environment_group="",
        environment="",
        endpoint_group="",
        endpoint="",
        result={},
        url="",
        expected="",
        message=message
    )

    deliver_alert_to_group(incident, "summary", alert_definitions)


def endpoints_check(endpoints, alert_definitions, db):
    logger.info("collecting endpoint health")

    for group in endpoints["groups"]:
        environment_group_id = group["id"]

        for environment in group["environments"]:
            environment_id = environment["id"]

            for endpoint_group in environment["endpoint-groups"]:
                endpoint_group_id = endpoint_group["id"]
                endpoint_group_enabled = endpoint_group["enabled"]

                if endpoint_group_enabled == "true":
                    for endpoint in endpoint_group["endpoints"]:
                        endpoint_id = endpoint["id"]
                        endpoint_url = endpoint["url"]

                        endpoint_expected = ""
                        if "expected" in endpoint:
                            endpoint_expected = endpoint["expected"]

                        endpoint_threshold = None
                        if "threshold" in endpoint:
                            endpoint_threshold = Threshold(endpoint["threshold"])

                        result = test_endpoint(url=endpoint_url, expected=endpoint_expected, threshold=endpoint_threshold)

                        incident = Incident(
                            timestamp=datetime.now(timezone.utc).astimezone().isoformat(),
                            environment_group=environment_group_id,
                            environment=environment_id,
                            endpoint_group=endpoint_group_id,
                            endpoint=endpoint_id,
                            result=result,
                            url=endpoint_url,
                            expected=endpoint_expected
                        )

                        alert_groups = get_endpoint_alert_groups(
                            endpoints=endpoints,
                            environment_group_id=environment_group_id,
                            environment_id=environment_id,
                            endpoint_group_id=endpoint_group_id,
                            endpoint_id=endpoint_id,
                            default_alert_groups=get_alerts_in_group("default", alert_definitions))

                        handle_result(
                            incident=incident,
                            alert_groups=alert_groups,
                            alert_definitions=alert_definitions,
                            db=db
                        )

                        if not lifecycle_continues():
                            return


def get_endpoint_alert_groups(endpoints, environment_group_id, environment_id, endpoint_group_id, endpoint_id, default_alert_groups):
    logger.debug("get_endpoint_alert_groups: {} {} {} {}".format(environment_group_id, environment_id, endpoint_group_id, endpoint_id))

    alert_groups = default_alert_groups

    environment_group = get_child_by_property(endpoints["groups"], "id", environment_group_id)
    environment = get_child_by_property(environment_group["environments"], "id", environment_id)
    endpoint_group = get_child_by_property(environment["endpoint-groups"], "id", endpoint_group_id)
    endpoint = get_child_by_property(endpoint_group["endpoints"], "id", endpoint_id)

    if "alert_groups" in environment_group:
        logger.debug("alert_groups in environment_group")
        alert_groups = environment_group["alert_groups"]

    if "alert_groups" in environment:
        logger.debug("alert_groups in environment")
        alert_groups = environment["alert_groups"]

    if "alert_groups" in endpoint_group:
        logger.debug("alert_groups in endpoint_group")
        alert_groups = endpoint_group["alert_groups"]

    if "alert_groups" in endpoint:
        logger.debug("alert_groups in endpoint")
        alert_groups = endpoint["alert_groups"]

    return alert_groups


def get_child_by_property(parent, property, target):
    for child in parent:
        if property in child and child[property] == target:
            return child

    return None


def test_endpoint(url, expected, threshold):
    logger.info("testing endpoint {}".format(url))
    if not lifecycle_continues():
        logger.info("test_endpoint: bailing")
        return False

    parse_result = urlparse(url)

    start_time = time.time()

    if parse_result.scheme == "http" or parse_result.scheme == "https":
        try:
            conn = None

            if parse_result.scheme == "http":
                conn = http.client.HTTPConnection(
                    host=parse_result.netloc,
                    timeout=settings.CONNECTION_TIMEOUT)
            else:
                conn = http.client.HTTPSConnection(
                    host=parse_result.netloc,
                    timeout=settings.CONNECTION_TIMEOUT)

            conn.request("GET", parse_result.path)
            status = str(conn.getresponse().status)
            logger.debug("status: {}, expected: {}".format(status, expected))
            if re.match(expected, status):
                # result was good but now check if timing was beyond threshold
                test_time = get_relative_time(start_time, time.time())
                logger.debug("response time was {}ms".format(int(round(getattr(test_time, "microsecond") / 1000.0))))
                threshold_result = None
                if threshold is not None:
                    threshold_result = threshold.result(test_time)

                if threshold_result is None or threshold_result.okay:
                    return {
                        "result": True,
                        "message": "OK"
                    }

                return {
                    "result": False,
                    "message": "BAD",
                    "threshold": threshold_result.result
                }

            return {
                "result": False,
                "actual": status,
                "message": "BAD"
            }

        except Exception as e:
            logger.debug("error during testing: {}".format(str(e)))
            pass

        return {
            "result": False,
            "message": "TIMEOUT"
        }


    elif parse_result.scheme == "tcp":
        s = socket.socket()
        try:
            s.settimeout(settings.CONNECTION_TIMEOUT)
            s.connect((parse_result.hostname, parse_result.port))
        except socket.timeout:
            logger.info(
                "tcp endpoint {} hit timeout".format(parse_result.netloc)
            )
            return {
                "result": False,
                "message": "TIMEOUT"
            }
        except Exception as e:
            logger.info(
                "tcp endpoint {} had a problem: {}".format(parse_result.netloc, e)
            )
            return {
                "result": False,
                "message": "BAD"
            }
        finally:
            s.close()
        # result was good but now check if timing was beyond threshold
        test_time = get_relative_time(start_time, time.time())

        logger.debug("response time was {}ms".format(int(round(getattr(test_time, "microsecond") / 1000.0))))

        threshold_result = None
        if threshold is not None:
            threshold_result = threshold.result(test_time)

        if threshold_result is None or threshold_result.okay:
            return {
                "result": True
            }

        return {
            "result": False,
            "message": "BAD",
            "threshold": threshold_result.result
        }


def get_relative_time(start_time, end_time):
    return relativedelta(microsecond=int(round((end_time-start_time) * 1000000)))


def handle_result(incident, alert_groups, alert_definitions, db):
    if not lifecycle_continues():
        logger.info("handle_result: bailing")
        return

    attrs = ["years", "months", "days", "hours", "minutes", "seconds", "microsecond"]
    human_readable = lambda delta: ["{} {}".format(getattr(delta, attr), getattr(delta, attr) > 1 and attr or attr[:-1])
        for attr in attrs if getattr(delta, attr)]

    logger.debug("result: timestamp: {}, environment_group: {} environment: {}, endpoint_group: {}, endpoint: {}, result: {}, url: {}, expected: {}".format(incident.timestamp, incident.environment_group, incident.environment, incident.endpoint_group, incident.endpoint, incident.result["result"], incident.url, incident.expected))

    if "actual" in incident.result:
        logger.info("actual: {}".format(incident.result["actual"]))

    if "threshold" in incident.result:
        logger.info("threshold: {}".format(incident.result["threshold"]))

    if db.active_exists(incident):
        # there's an existing alert for this tuple
        active = db.get_active(incident)
        if incident.result["result"]:
            # existing alert cleared
            logger.info("cleared alert")

            delta = relativedelta(seconds=time.time()-active["timestamp"])
            incident.message = "{} {} {} {} now OK after {}\n({})".format(incident.environment_group, incident.environment, incident.endpoint_group, incident.endpoint,
                    ", ".join(human_readable(delta)), incident.url)

            db.remove_active(incident)

            deliver_alert_to_groups(incident, alert_groups, alert_definitions)
        else:
            # existing alert continues
            logger.debug("alert continues")
            pass

    else:
        # no existing alert for this tuple
        if incident.result["result"]:
            # result was good
            logger.debug("no alert")
            pass
        else:
            # result was bad
            logger.info("new alert recorded")
            incident.timestamp = time.time()

            if "threshold" in incident.result:
                incident.presentation_message = "result was {}".format(incident.result["threshold"])
                incident.message = "{} {} {} {} response {}".format(incident.environment_group, incident.environment, incident.endpoint_group, incident.endpoint, incident.result["threshold"])
            else:
                incident.presentation_message = "result was {}".format(incident.result["message"])
                incident.message = "{} {} {} {} expected {}".format(incident.environment_group, incident.environment, incident.endpoint_group, incident.endpoint, incident.expected)

                if "actual" in incident.result:
                    incident.presentation_message = "{}, actual: {}".format(incident.presentation_message, incident.result["actual"])
                    incident.message = "{}, actual: {}".format(incident.message, incident.result["actual"])
                else:
                    incident.message = "{}, actual: {}".format(incident.message, incident.result["message"])

                incident.message = "{}\n({})".format(incident.message, incident.url)

            db.save_active(incident)

            deliver_alert_to_groups(incident, alert_groups, alert_definitions)


if __name__ == "__main__":
    if settings.DEBUG:
        logzero.loglevel(logging.DEBUG)
    else:
        logzero.loglevel(logging.INFO)

    main()
