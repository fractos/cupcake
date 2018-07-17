from logzero import logger
from urllib.parse import urlparse
from datetime import datetime, timezone
import re
import socket
import http.client
import json
import time
import requests
import settings

def main():
    logger.info("starting...")

    while True:
        lifecycle()
        time.sleep(settings.SLEEP_SECONDS)


def lifecycle():
    endpoint_definitions = json.loads(
        open(settings.ENDPOINT_DEFINITIONS_FILE).read()
    )

    alert_definitions = json.loads(
        open(settings.ALERT_DEFINITIONS_FILE).read()
    )

    # collect endpoint results
    endpoints_check(endpoint_definitions, alert_definitions)


def endpoints_check(endpoints, alerts):
    logger.info('collecting endpoint health')

    for group in endpoints['groups']:

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
                            environment=environment_id,
                            namespace=endpoint_group_name,
                            key=endpoint_name,
                            result=test_endpoint(url=endpoint_url, expected=endpoint_expected),
                            url=endpoint_url,
                            expected=endpoint_expected,
                            alerts=alerts
                        )


def test_endpoint(url, expected):
    logger.info('testing endpoint ' + url)
    parse_result = urlparse(url)

    if parse_result.scheme == 'http' or parse_result.scheme == 'https':
        try:
            conn = None

            if parse_result.scheme == 'http':
                logger.debug('starting http connection')
                conn = http.client.HTTPConnection(parse_result.netloc)
            else:
                logger.debug('starting https connection')
                conn = http.client.HTTPSConnection(parse_result.netloc)

            conn.request('GET', parse_result.path)
            status = str(conn.getresponse().status)
            logger.debug('status: %s, expected: %s' % (status, expected))
            if re.match(expected, status):
                return {
                    "result": True
                }
            else:
                return {
                    "result": False,
                    "actual": status
                }

        except Exception:
            pass

        return {
            "result": False
        }


    elif parse_result.scheme == 'tcp':
        s = socket.socket()
        try:
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


def handle_result(environment, namespace, key, result, expected, alerts, url="none"):
    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    logger.info('result: timestamp: %s, environment: %s, namespace: %s, key: %s, result: %s, url: %s, expected: %s'
        % (timestamp, environment, namespace, key, result['result'], url, expected))
    if 'actual' in result:
        logger.info('actual: %s' % result['actual'])

    message = '%s %s %s %s' % (environment, namespace, key, result)

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
