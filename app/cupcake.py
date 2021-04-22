from logzero import logger
import logging
import logzero
from urllib.parse import urlparse
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from concurrent.futures.thread import ThreadPoolExecutor
import re
import socket
import http.client
import json
import time
import requests
import signal
import os
import boto3
import uuid
from models import Incident, Threshold, Metric, Endpoint
from alerts import deliver_alert_to_groups, deliver_alert_to_group, get_alerts_in_group
from metrics import deliver_metric_to_groups, get_metrics_in_group
import settings

requested_to_quit = False
last_summary_emitted = 0

endpoint_definitions = None
alert_definitions = None
metrics_definitions = None
db = None

def main():
    logger.info("starting...")

    setup_signal_handling()

    global db
    db = settings.get_database()

    while lifecycle_continues():
        lifecycle()

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


def get_file_or_s3(uri):
    logger.info("getting file URI %s" % uri)

    if uri.lower().startswith("s3://"):
        s3 = boto3.resource("s3")
        parse_result = urlparse(uri)
        s3_object = s3.Object(parse_result.netloc, parse_result.path.lstrip("/"))
        return s3_object.get()["Body"].read().decode("utf-8")

    return open(uri).read()


def lifecycle():
    global last_summary_emitted
    global endpoint_definitions
    global alert_definitions
    global metrics_definitions

    endpoint_definitions = json.loads(
        get_file_or_s3(settings.ENDPOINT_DEFINITIONS_FILE)
    )

    alert_definitions = json.loads(
        get_file_or_s3(settings.ALERT_DEFINITIONS_FILE)
    )

    metrics_definitions = json.loads(
        get_file_or_s3(settings.METRICS_DEFINITIONS_FILE)
    )

    if settings.SUMMARY_ENABLED:
        seconds=time.time()-last_summary_emitted
        if seconds >= settings.SUMMARY_SLEEP_SECONDS:
            last_summary_emitted = time.time()
            emit_summary()

    endpoints_check()


def emit_summary():
    """
    Show a summary via a subset of notification types
    """
    logger.info("emit summary")

    global endpoint_definitions
    global alert_definitions
    global db

    number_of_endpoints = 0

    for group in endpoint_definitions["groups"]:
        for environment in group["environments"]:
            for endpoint_group in environment["endpoint-groups"]:
                if endpoint_group["enabled"] == "true":
                    for _ in endpoint_group["endpoints"]:
                        number_of_endpoints = number_of_endpoints + 1

    endpoint_plural = "s"

    if number_of_endpoints == 1:
        endpoint_plural = ""

    message = "Cupcake is alive and currently monitoring {} endpoint{}.".format(number_of_endpoints, endpoint_plural)

    actives = db.get_all_actives()
    actives_message = ""

    for active in actives:
        actives_message = actives_message + "{} since {}\n".format(active["message"], datetime.utcfromtimestamp(active["timestamp"]).strftime('%Y-%m-%d %H:%M:%S'))

    if len(actives) == 0:
        message = message + "\n\nCupcake is not currently aware of any alerts."
    else:
        message = message + "\n\nCupcake is aware of the following alerts:\n%s" % actives_message

    deliver_alert_to_group(
        incident=Incident(
            timestamp=time.time(),
            message=message
        ),
        alert_group_id="summary",
        alert_definitions=alert_definitions
    )


def endpoints_check():
    global endpoint_definitions
    global alert_definitions
    global metrics_definitions
    global db

    thread_args = []

    logger.info("collecting endpoint health")

    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:

        for group in endpoint_definitions["groups"]:
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

                            if "appendTraceID" in endpoint and endpoint["appendTraceID"]:

                                # default argument key
                                trace_argument_key = "cupcake_trace_id"

                                # use custom key if provided
                                if "traceArgumentKey" in endpoint:
                                    trace_argument_key = endpoint["traceArgumentKey"]

                                endpoint_url = create_or_append_query_string(
                                    original=endpoint_url,
                                    argument="{}={}".format(trace_argument_key, get_trace_id())
                                )

                            if "appendAttempt" in endpoint and endpoint["appendAttempt"]:

                                # default argument key
                                append_attempt_key = "cupcake_attempt"

                                # use custom key if provided
                                if "attemptArgumentKey" in endpoint:
                                    append_attempt_key = endpoint["attemptArgumentKey"]

                                endpoint_url = create_or_append_query_string(
                                    original=endpoint_url,
                                    argument="{}=##CUPCAKE_ATTEMPT##".format(append_attempt_key)
                                )

                            endpoint_expected = ""
                            if "expected" in endpoint:
                                endpoint_expected = endpoint["expected"]

                            endpoint_threshold = None
                            if "threshold" in endpoint:
                                endpoint_threshold = Threshold(endpoint["threshold"])

                            endpoint_model = Endpoint(
                                environment_group = environment_group_id,
                                environment = environment_id,
                                endpoint_group = endpoint_group_id,
                                endpoint = endpoint_id,
                                url = endpoint_url
                            )

                            metrics_groups = get_endpoint_default(
                                model=endpoint_model,
                                property="metrics-groups",
                                default_value=["default"]
                            )

                            alert_groups = get_endpoint_default(
                                model=endpoint_model,
                                property="alert-groups",
                                default_value=get_alerts_in_group("default", alert_definitions)
                            )

                            executor.submit(run_test, endpoint_model, metrics_groups, alert_groups, endpoint_expected, endpoint_threshold)


def get_trace_id():
    return str(uuid.uuid4())


def create_or_append_query_string(original, argument):
    if "?" in original:
        return "{}&{}".format(original, argument)
    # else...
    return "{}?{}".format(original, argument)


def run_test(endpoint_model, metrics_groups, alert_groups, endpoint_expected, endpoint_threshold):
    attempt = 0
    keep_trying = True
    incident_timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    original_endpoint_url = endpoint_model.url
    while keep_trying:

        if "##CUPCAKE_ATTEMPT##" in original_endpoint_url:
            endpoint_model.url = original_endpoint_url.replace("##CUPCAKE_ATTEMPT##", str(attempt + 1))

        result = test_endpoint(
            endpoint=endpoint_model,
            expected=endpoint_expected,
            threshold=endpoint_threshold,
            metrics_groups=metrics_groups
        )
        if not result["result"] and result["message"] == "TIMEOUT":
            attempt = attempt + 1
            if attempt <= 3:
                logger.info("re-testing timed out endpoint ({}) (attempt {} failed)".format(endpoint_model.url, attempt))
                keep_trying = True
                continue
        break

    incident = Incident(
        timestamp=incident_timestamp,
        endpoint=endpoint_model,
        result=result,
        expected=endpoint_expected
    )

    handle_result(
        incident=incident,
        alert_groups=alert_groups
    )


def get_endpoint_default(model, property, default_value):
    global endpoint_definitions

    result = default_value

    environment_group = get_child_by_property(endpoint_definitions["groups"], "id", model.environment_group)
    environment = get_child_by_property(environment_group["environments"], "id", model.environment)
    endpoint_group = get_child_by_property(environment["endpoint-groups"], "id", model.endpoint_group)
    endpoint = get_child_by_property(endpoint_group["endpoints"], "id", model.endpoint)

    if property in environment_group:
        result = environment_group[property]

    if property in environment:
        result = environment[property]

    if property in endpoint_group:
        result = endpoint_group[property]

    if property in endpoint:
        result = endpoint[property]

    return result


def get_child_by_property(parent, property, target):
    for child in parent:
        if property in child and child[property] == target:
            return child

    return None


def test_endpoint(endpoint, expected, threshold, metrics_groups):
    global metrics_definitions

    logger.info("testing endpoint {}".format(endpoint.url))
    if not lifecycle_continues():
        logger.info("test_endpoint: bailing")
        return False

    parse_result = urlparse(endpoint.url)

    start_time = time.time()
    test_time = None

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

            request_path = "{}{}".format(parse_result.path, "?{}".format(parse_result.query) if len(parse_result.query) > 0 else "")
            logger.debug("request path: {}".format(request_path))
            conn.request("GET", request_path)
            http_response = conn.getresponse()
            status = str(http_response.status)
            logger.debug("status: {}, expected: {}".format(status, expected))
            if re.match(expected, status):
                # result was good but now check if timing was beyond threshold
                test_time = get_relative_time(start_time, time.time())
                logger.debug("response time was {}ms".format(int(round(getattr(test_time, "microsecond") / 1000.0))))

                metrics_record_response_time(
                    endpoint=endpoint,
                    timestamp=time.time(),
                    response_time=int(round(getattr(test_time, "microsecond") / 1000.0)),
                    metrics_groups=metrics_groups
                )

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

            test_time = get_relative_time(start_time, time.time())
            logger.debug("response time was {}ms".format(int(round(getattr(test_time, "microsecond") / 1000.0))))

            metrics_record_response_time(
                endpoint=endpoint,
                timestamp=time.time(),
                response_time=int(round(getattr(test_time, "microsecond") / 1000.0)),
                metrics_groups=metrics_groups
            )

            if settings.SHOW_BODY_IN_DEBUG_ON_UNEXPECTED_STATUS:
                body = http_response.read()
                logger.debug("body for {} was {}", endpoint.url, body)

            return {
                "result": False,
                "actual": status,
                "message": "BAD"
            }

        except Exception as e:
            logger.debug("error during testing: {}".format(str(e)))
            pass

        test_time = get_relative_time(start_time, time.time())

        metrics_record_response_time(
            endpoint=endpoint,
            timestamp=time.time(),
            response_time=int(round(getattr(test_time, "microsecond") / 1000.0)),
            metrics_groups=metrics_groups
        )

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

            test_time = get_relative_time(start_time, time.time())

            metrics_record_response_time(
                endpoint=endpoint,
                timestamp=time.time(),
                response_time=int(round(getattr(test_time, "microsecond") / 1000.0)),
                metrics_groups=metrics_groups
            )

            return {
                "result": False,
                "message": "TIMEOUT"
            }

        except Exception as e:

            test_time = get_relative_time(start_time, time.time())

            metrics_record_response_time(
                endpoint=endpoint,
                timestamp=time.time(),
                response_time=int(round(getattr(test_time, "microsecond") / 1000.0)),
                metrics_groups=metrics_groups
            )

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

        metrics_record_response_time(
            endpoint=endpoint,
            timestamp=time.time(),
            response_time=int(round(getattr(test_time, "microsecond") / 1000.0)),
            metrics_groups=metrics_groups
        )

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


def metrics_record_response_time(endpoint, timestamp, response_time, metrics_groups):
    global metrics_definitions

    logger.debug("metrics_record_response_time({}, {}, {})".format(
        endpoint.url, str(timestamp), str(response_time)))

    metric = Metric(
        endpoint=endpoint,
        timestamp=timestamp,
        name='RESPONSE-TIME',
        data=response_time
    )

    deliver_metric_to_groups(metric, metrics_groups, metrics_definitions)


def get_relative_time(start_time, end_time):
    return relativedelta(microsecond=int(round((end_time-start_time) * 1000000)))


def handle_result(incident, alert_groups):
    global alert_definitions
    global db

    if not lifecycle_continues():
        logger.info("handle_result: bailing")
        return

    attrs = ["years", "months", "days", "hours", "minutes", "seconds", "microsecond"]
    human_readable = lambda delta: ["%d %s" % (getattr(delta, attr), getattr(delta, attr) > 1 and attr or attr[:-1])
        for attr in attrs if getattr(delta, attr)]

    logger.debug("result: timestamp: {}, environment_group: {} environment: {}, endpoint_group: {}, endpoint: {}, result: {}, url: {}, expected: {}".format(
        incident.timestamp,
        incident.endpoint.environment_group,
        incident.endpoint.environment,
        incident.endpoint.endpoint_group,
        incident.endpoint.endpoint,
        incident.result["result"],
        incident.endpoint.url,
        incident.expected
    ))

    if "actual" in incident.result:
        logger.info("actual for {}: {}".format(incident.endpoint.url, incident.result["actual"]))

    if "threshold" in incident.result:
        logger.info("threshold for {}: {}".format(incident.endpoint.url, incident.result["threshold"]))

    if db.active_exists(incident):
        # there's an existing alert for this tuple
        active = db.get_active(incident)
        if incident.result["result"]:
            # existing alert cleared
            logger.info("cleared alert for {}".format(incident.endpoint.url))

            delta = relativedelta(seconds=time.time()-active["timestamp"])
            incident.message = "{} now OK after {}\n".format(
                repr(incident.endpoint),
                ", ".join(human_readable(delta))
            )

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
                incident.message = "{} response {}".format(
                    repr(incident.endpoint),
                    incident.result["threshold"]
                )
            else:
                incident.presentation_message = "result was {}".format(incident.result["message"])
                incident.message = "{} expected {}".format(
                    repr(incident.endpoint),
                    incident.expected
                )

                if "actual" in incident.result:
                    incident.presentation_message = "{}, actual: {}".format(
                        incident.presentation_message,
                        incident.result["actual"]
                    )
                    incident.message = "{}, actual: {}".format(
                        incident.message,
                        incident.result["actual"]
                    )
                else:
                    incident.message = "{}, actual: {}".format(
                        incident.message,
                        incident.result["message"]
                    )

                # incident.message = "{}\n({})".format(
                #     incident.message,
                #     incident.endpoint.url
                # )

            db.save_active(incident)

            deliver_alert_to_groups(incident, alert_groups, alert_definitions)


if __name__ == "__main__":
    if settings.DEBUG:
        logzero.loglevel(logging.DEBUG)
    else:
        logzero.loglevel(logging.INFO)

    main()
