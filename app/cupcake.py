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
    print("starting...")

    setup_signal_handling()

    global db
    db = settings.get_database()

    while lifecycle_continues():
        lifecycle()

        if lifecycle_continues():
            print("sleeping for %s seconds..." % settings.SLEEP_SECONDS)
            for _ in range(settings.SLEEP_SECONDS):
                if lifecycle_continues():
                    time.sleep(1)


def lifecycle_continues():
    return not requested_to_quit


def signal_handler(signum, frame):
    print("Caught signal %s" % signum)
    global requested_to_quit
    requested_to_quit = True


def setup_signal_handling():
    print("setting up signal handling")
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def get_file_or_s3(uri):
    print("getting file URI %s" % uri)

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
    print("emit summary")

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

    message = "Cupcake is alive and currently monitoring {} endpoints.".format(number_of_endpoints)

    actives = db.get_all_actives()
    actives_message = ""

    for active in actives:
        actives_message = actives_message + "{}\n".format(active["message"])

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

    print("collecting endpoint health")

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


def run_test(endpoint_model, metrics_groups, alert_groups, endpoint_expected, endpoint_threshold):
    attempt = 0
    keep_trying = True
    while keep_trying:
        result = test_endpoint(
            endpoint=endpoint_model,
            expected=endpoint_expected,
            threshold=endpoint_threshold,
            metrics_groups=metrics_groups
        )
        if not result["result"] and result["message"] == "TIMEOUT":
            attempt = attempt + 1
            if attempt <= 3:
                print("re-testing timed out endpoint ({}) (attempt {} failed)".format(endpoint_model.url, attempt))
                keep_trying = True
                continue
        break

    incident = Incident(
        timestamp=datetime.now(timezone.utc).astimezone().isoformat(),
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

    print("testing endpoint {}".format(endpoint.url))
    if not lifecycle_continues():
        print("test_endpoint: bailing")
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

            conn.request("GET", parse_result.path)
            status = str(conn.getresponse().status)
            print("status: {}, expected: {}".format(status, expected))
            if re.match(expected, status):
                # result was good but now check if timing was beyond threshold
                test_time = get_relative_time(start_time, time.time())
                print("response time was {}ms".format(int(round(getattr(test_time, "microsecond") / 1000.0))))

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
            print("response time was {}ms".format(int(round(getattr(test_time, "microsecond") / 1000.0))))

            metrics_record_response_time(
                endpoint=endpoint,
                timestamp=time.time(),
                response_time=int(round(getattr(test_time, "microsecond") / 1000.0)),
                metrics_groups=metrics_groups
            )

            return {
                "result": False,
                "actual": status,
                "message": "BAD"
            }

        except Exception as e:
            print("error during testing: {}".format(str(e)))
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
            print("tcp endpoint {} hit timeout".format(parse_result.netloc))

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

            print("tcp endpoint {} had a problem: {}".format(parse_result.netloc, e))
            return {
                "result": False,
                "message": "BAD"
            }

        finally:
            s.close()
        # result was good but now check if timing was beyond threshold
        test_time = get_relative_time(start_time, time.time())

        print("response time was {}ms".format(int(round(getattr(test_time, "microsecond") / 1000.0))))

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

    print("metrics_record_response_time({}, {}, {})".format(
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
        print("handle_result: bailing")
        return

    attrs = ["years", "months", "days", "hours", "minutes", "seconds", "microsecond"]
    human_readable = lambda delta: ["%d %s" % (getattr(delta, attr), getattr(delta, attr) > 1 and attr or attr[:-1])
        for attr in attrs if getattr(delta, attr)]

    print("result: timestamp: {}, environment_group: {} environment: {}, endpoint_group: {}, endpoint: {}, result: {}, url: {}, expected: {}".format(
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
        print("actual: {}".format(incident.result["actual"]))

    if "threshold" in incident.result:
        print("threshold: {}".format(incident.result["threshold"]))

    if db.active_exists(incident):
        # there's an existing alert for this tuple
        active = db.get_active(incident)
        if incident.result["result"]:
            # existing alert cleared
            print("cleared alert")

            delta = relativedelta(seconds=time.time()-active["timestamp"])
            incident.message = "{} now OK after {}\n".format(
                repr(incident.endpoint),
                ", ".join(human_readable(delta))
            )

            db.remove_active(incident)

            deliver_alert_to_groups(incident, alert_groups, alert_definitions)
        else:
            # existing alert continues
            print("alert continues")
            pass

    else:
        # no existing alert for this tuple
        if incident.result["result"]:
            # result was good
            print("no alert")
            pass
        else:
            # result was bad
            print("new alert recorded")
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

                incident.message = "{}\n({})".format(
                    incident.message,
                    incident.endpoint.url
                )

            db.save_active(incident)

            deliver_alert_to_groups(incident, alert_groups, alert_definitions)


if __name__ == "__main__":
    main()
