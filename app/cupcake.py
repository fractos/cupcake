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
import signal
import boto3
import uuid
from models import Incident, Threshold, Metric, Endpoint
from alerts import deliver_alert_to_groups, deliver_alert_to_group, get_alerts_in_group
from metrics import deliver_metric_to_groups
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
        seconds = time.time() - last_summary_emitted
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

    # build a list of monitored results, for ease of validating actives
    monitored = []

    for group in endpoint_definitions["groups"]:
        for environment in group["environments"]:
            for endpoint_group in environment["endpoint-groups"]:
                for endpoint in endpoint_group["endpoints"]:
                    endpoint_group_enabled = endpoint_group["enabled"]
                    monitor_id = get_monitor_identifier(group["id"], environment["id"], endpoint_group["id"],
                                                        endpoint["id"], endpoint_group_enabled)
                    monitored.append(monitor_id)
                    if endpoint_group_enabled == "true":
                        number_of_endpoints = number_of_endpoints + 1

    endpoint_plural = "s"

    if number_of_endpoints == 1:
        endpoint_plural = ""

    message = f"Cupcake is alive and currently monitoring {number_of_endpoints} endpoint{endpoint_plural}."

    message = process_active_alerts(db, monitored, message)

    deliver_alert_to_group(
        incident=Incident(
            timestamp=time.time(),
            message=message
        ),
        alert_group_id="summary",
        alert_definitions=alert_definitions
    )


def process_active_alerts(db, monitored, message):
    actives = db.get_all_actives()
    actives_message = ""
    for active in actives:
        display_time = datetime.utcfromtimestamp(active["timestamp"]).strftime('%Y-%m-%d %H:%M:%S')

        environment_group_id = active["environment_group"]
        environment_id = active["environment"]
        endpoint_group_id = active["endpoint_group"]
        endpoint_id = active["endpoint"]
        monitor_id = get_monitor_identifier(environment_group_id, environment_id, endpoint_group_id, endpoint_id,
                                            "true")

        if not settings.REMOVE_UNKNOWN_ACTIVES or monitor_id in monitored:
            actives_message = f"{actives_message}{active['message']} since {display_time}\n"
        else:
            logger.info(f"removing active alert as not in alert definition: {monitor_id}")
            actives_message = f"{actives_message}removing alert: {active['message']} since {display_time}\n"
            endpoint_model = Endpoint(
                environment_group=environment_group_id,
                environment=environment_id,
                endpoint_group=endpoint_group_id,
                endpoint=endpoint_id,
                url=active["url"]
            )

            incident = Incident(
                timestamp=datetime.now(timezone.utc).astimezone().isoformat(),
                endpoint=endpoint_model
            )
            db.remove_active(incident)

    if len(actives) == 0:
        message = message + "\n\nCupcake is not currently aware of any alerts."
    else:
        message = message + f"\n\nCupcake is aware of the following alerts:\n{actives_message}"
    return message


def get_monitor_identifier(environment_group, environment, endpoint_group, endpoint, active):
    return "%s|%s|%s|%s|%s" % (environment_group, environment, endpoint_group, endpoint, active)


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
                                trace_argument_key = endpoint.get("traceArgumentKey", "cupcake_trace_id")

                                endpoint_url = create_or_append_query_string(
                                    original=endpoint_url,
                                    argument="{}={}".format(trace_argument_key, get_trace_id())
                                )

                            if "appendAttempt" in endpoint and endpoint["appendAttempt"]:
                                # have default argument key, use custom key if provided
                                append_attempt_key = endpoint.get("attemptArgumentKey", "cupcake_attempt")

                                endpoint_url = create_or_append_query_string(
                                    original=endpoint_url,
                                    argument="{}=##CUPCAKE_ATTEMPT##".format(append_attempt_key)
                                )

                            endpoint_expected = endpoint.get("expected", "")

                            endpoint_threshold = None
                            if "threshold" in endpoint:
                                endpoint_threshold = Threshold(endpoint["threshold"])

                            retry = endpoint.get("retry", 0)
                            timeout = endpoint.get("timeout", 0)

                            endpoint_model = Endpoint(
                                environment_group=environment_group_id,
                                environment=environment_id,
                                endpoint_group=endpoint_group_id,
                                endpoint=endpoint_id,
                                url=endpoint_url,
                                retry=retry,
                                timeout=timeout
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

                            executor.submit(run_test, endpoint_model, metrics_groups, alert_groups, endpoint_expected,
                                            endpoint_threshold)


def get_trace_id():
    return str(uuid.uuid4())


def create_or_append_query_string(original, argument):
    if "?" in original:
        return f"{original}&{argument}"
    # else...
    return f"{original}?{argument}"


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

        # Retry TIMEOUTs 3 times but only if not already retried via endpoint config
        if not result["result"] and result["message"] == "TIMEOUT" and not result.get("retried", False):
            attempt = attempt + 1
            if attempt <= 3:
                logger.info(
                    f"re-testing timed out endpoint ({endpoint_model}) (attempt {attempt} failed)")
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

    logger.info(f"testing endpoint {endpoint.url}")
    if not lifecycle_continues():
        logger.info("test_endpoint: bailing")
        return False

    parse_result = urlparse(endpoint.url)

    count = 0
    result = {}

    while count <= endpoint.retry:
        count += 1
        if parse_result.scheme == "http" or parse_result.scheme == "https":
            result = http_check(parse_result, endpoint, expected, threshold, metrics_groups)
        elif parse_result.scheme == "tcp":
            result = tcp_check(parse_result, endpoint, threshold, metrics_groups)

        if result.get("result", False):
            break

    if count > 1:
        result["retried"] = True

    return result


def http_check(parse_result, endpoint, expected, threshold, metrics_groups):
    start_time = time.time()
    try:
        conn = None

        timeout = settings.CONNECTION_TIMEOUT
        if endpoint.timeout:
            timeout = endpoint.timeout

        if parse_result.scheme == "http":
            conn = http.client.HTTPConnection(
                host=parse_result.netloc,
                timeout=timeout)
        else:
            conn = http.client.HTTPSConnection(
                host=parse_result.netloc,
                timeout=timeout)

        request_path = "{}{}".format(parse_result.path,
                                     "?{}".format(parse_result.query) if len(parse_result.query) > 0 else "")
        logger.debug(f"request path: {request_path}")
        conn.request("GET", request_path)
        http_response = conn.getresponse()
        status = str(http_response.status)
        logger.debug(f"status: {status}, expected: {expected}")
        if re.match(expected, status):
            # result was good but now check if timing was beyond threshold
            return threshold_check(start_time, endpoint, metrics_groups, threshold)

        test_time = get_relative_time(start_time, time.time())
        response_time = int(round(getattr(test_time, "microsecond") / 1000.0))
        logger.debug(f"response time was {response_time}ms")

        metrics_record_response_time(
            endpoint=endpoint,
            timestamp=time.time(),
            response_time=response_time,
            metrics_groups=metrics_groups
        )

        if settings.SHOW_BODY_IN_DEBUG_ON_UNEXPECTED_STATUS:
            body = http_response.read()
            logger.debug(f"body for {endpoint.url} was {body}")

        return {
            "result": False,
            "actual": status,
            "message": "BAD"
        }

    except Exception as e:
        logger.debug(f"error during testing: {str(e)}")
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


def tcp_check(parse_result, endpoint, threshold, metrics_groups):
    start_time = time.time()

    s = socket.socket()
    try:
        s.settimeout(settings.CONNECTION_TIMEOUT)
        s.connect((parse_result.hostname, parse_result.port))
    except socket.timeout:
        logger.info(f"tcp endpoint {parse_result.netloc} hit timeout")

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

        logger.info(f"tcp endpoint {parse_result.netloc} had a problem: {e}")
        return {
            "result": False,
            "message": "BAD"
        }

    finally:
        s.close()

    # result was good but now check if timing was beyond threshold
    return threshold_check(start_time, endpoint, metrics_groups, threshold)


def threshold_check(start_time, endpoint, metrics_groups, threshold):
    test_time = get_relative_time(start_time, time.time())
    response_time = int(round(getattr(test_time, "microsecond") / 1000.0))
    logger.debug(f"response time was {response_time}ms")

    metrics_record_response_time(
        endpoint=endpoint,
        timestamp=time.time(),
        response_time=response_time,
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
    return relativedelta(microsecond=int(round((end_time - start_time) * 1000000)))


def handle_result(incident, alert_groups):
    global alert_definitions
    global db

    if not lifecycle_continues():
        logger.info("handle_result: bailing")
        return

    attrs = ["years", "months", "days", "hours", "minutes", "seconds", "microsecond"]
    human_readable = lambda delta: ["%d %s" % (getattr(delta, attr), getattr(delta, attr) > 1 and attr or attr[:-1])
                                    for attr in attrs if getattr(delta, attr)]

    logger.debug(
        "result: timestamp: {}, environment_group: {} environment: {}, endpoint_group: {}, endpoint: {}, result: {}, url: {}, expected: {}".format(
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
        logger.info(f"actual for {incident.endpoint.url}: {incident.result['actual']}")

    if "threshold" in incident.result:
        logger.info(f"threshold for {incident.endpoint.url}: {incident.result['threshold']}")

    if db.active_exists(incident):
        # there's an existing alert for this tuple
        active = db.get_active(incident)
        if incident.result["result"]:
            # existing alert cleared
            logger.info(f"cleared alert for {incident.endpoint.url}")

            delta = relativedelta(seconds=time.time() - active["timestamp"])
            incident.message = f"{repr(incident.endpoint)} now OK after {', '.join(human_readable(delta))}\n"

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
                incident.message = f"{repr(incident.endpoint)} response {incident.result['threshold']}"
            else:
                incident.presentation_message = f"result was {incident.result['message']}"
                incident.message = f"{repr(incident.endpoint)} expected {incident.expected}"

                if "actual" in incident.result:
                    incident.presentation_message = f"{incident.presentation_message}, actual: {incident.result['actual']}"
                    incident.message = f"{incident.message}, actual: {incident.result['actual']}"
                else:
                    incident.message = f"{incident.message}, actual: {incident.result['message']}"

            db.save_active(incident)

            deliver_alert_to_groups(incident, alert_groups, alert_definitions)


if __name__ == "__main__":
    if settings.DEBUG:
        logzero.loglevel(logging.DEBUG)
    else:
        logzero.loglevel(logging.INFO)

    main()
