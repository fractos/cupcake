# Cupcake

[![Build Status](https://travis-ci.org/fractos/cupcake.svg?branch=master)](https://travis-ci.org/fractos/cupcake)
[![Docker Pulls](https://img.shields.io/docker/pulls/fractos/cupcake.svg?style=for-the-badge)](https://hub.docker.com/r/fractos/cupcake/)

## Table of Contents

<!-- TOC orderedList:false -->

- [Cupcake](#cupcake)
    - [Table of Contents](#table-of-contents)
    - [Environment variables](#environment-variables)
    - [sqlite](#sqlite)
    - [PostgreSQL](#postgresql)
    - [Endpoint definition file](#endpoint-definition-file)
        - [Example](#example)
    - [Alert definition file](#alert-definition-file)
        - [Example](#example-1)
    - [Metrics definitions file](#metrics-definitions-file)
        - [Example](#example-2)

<!-- /TOC -->

This is a very simple HTTP, HTTPS and TCP endpoint monitor intended to be the simplest thing that works.

It will work through a file that defines groups of environments and endpoints.

If an endpoint times out for connection or if an HTTP/HTTPS endpoint returns a different status code than is expected,
then an alert is processed. Cupcake records previous alerts in a backing database and therefore will only alert once for
a failure, but will note a return to service for an endpoint together with an approximate length of time that the outage
occurred for.

Expected status codes for HTTP/HTTPS services is specified with a regular expression.

At the moment Cupcake is able to emit alerts via a webhook URL such as the one used by Slack's custom webhook
integration and also by sending a JSON blob to an SNS topic.

If the environment variable `SUMMARY_ENABLED` is "True", Cupcake will emit a summary digest at startup and
every `SUMMARY_SLEEP_SECONDS` afterwards to the alert group called `summary` (
see [Alert definition file](#alert-definition-file), below).

## Environment variables

| Name                       | Description                                                              | Default                        |
|----------------------------|--------------------------------------------------------------------------|--------------------------------|
| DEBUG                      | Whether to produce debug messages in the log                             | False                          |
| SLEEP_SECONDS              | Number of seconds to yield between runs                                  | 60                             |
| ENDPOINT_DEFINITIONS_FILE  | Full path or S3 URL of endpoint definitions file                         | /opt/app/config/endpoints.json |
| ALERT_DEFINITIONS_FILE     | Full path or S3 URL of alert definitions file                            | /opt/app/config/alerts.json    |
| METRICS_DEFINITIONS_FILE   | Full path or S3 URL of metrics definitions file                          | /opt/app/config/metrics.json   |
| CONNECTION_TIMEOUT_SECONDS | Number of seconds before HTTP(S) and TCP connections will timeout        | 10                             |
| DB_TYPE                    | Type of database to use. Possible values: `sqlite` or `postgresql`       | sqlite                         |
| SUMMARY_ENABLED            | Whether to emit a summary / digest message to a subset of alert channels | True                           |
| SUMMARY_SLEEP_SECONDS      | Number of seconds between emitting summary digests                       | 86400                          |
| REMOVE_UNKNOWN_ACTIVES     | Whether to delete active alerts that are no longer present in alert defs | False                          |

Note:

It is important to have CONNECTION_TIMEOUT_SECONDS set to a value less than 30, as when a process in a containerised
environment such as ECS is redeployed or stopped then it will be given a SIGTERM signal and a 30 second timeout before a
SIGKILL signal is sent that will kill the process immediately. Cupcake tests whether it has been requested to stop after
each endpoint measurement and intercepts SIGTERM and SIGINT in order to try and quit as soon as possible after receiving
them.

## sqlite

To use sqlite as the backing database, set the following:

```
DB_TYPE='sqlite'
```

| Name    | Description                                   | Default    |
|---------|-----------------------------------------------|------------|
| DB_NAME | This is the full path of the .db file to use. | cupcake.db |

## PostgreSQL

To use PostgreSQL as the backing database, set the following:

```
DB_TYPE='postgresql'
```

| Name        | Description                                              | Default |
|-------------|----------------------------------------------------------|---------|
| DB_NAME     | This is the database name                                |         |
| DB_HOST     | This is the database host and port in `host:port` format |         |
| DB_USER     | This is the username to connect to database with         |         |
| DB_PASSWORD | This is the password to connect to database with         |         |

## Endpoint definition file

Endpoints are organised in the following hierarchy:

```
environment_group (e.g. "customer 1")
|
+- environment (e.g. "production")
   |
   +- endpoint_group (e.g. "services")
      |
      +- endpoint (e.g. "api")
```

This gives a great deal of flexibility and range for defining collections of endpoints.

### Example

The following defines an environment group called "customer ABC" which has an environment called "production". Within
that environment are two endpoint groups - "external" and "internal".

The "external" endpoint group contains an HTTPS URL for a website and a regular expression that defines the HTTP status
code that it expects to receive (any status code in range 2xx). An optional GUID will be added to the URL query string
with the key `cupcake_trace_id` (which is the default). An optional attempt number will be added to the URL query string
with the key `cupcake_attempt` (which is the default). The URL including the TraceID will be emitted in any alert
incident that occurs allowing this to be located in server access logs. `retry` signifies to retry this endpoint 2 times
if failure encountered. By default all endpoints that fail due to a timeout are retried 3 times - the `retry` value
overrides that.

The "internal" endpoint group contains a TCP URL for a Redis server. It is assumed for this example that Cupcake is
situated on a server that is inside the private network and therefore is able to lookup a host named "redis.internal"
using some kind of internal DNS scheme (e.g. Route53).

The website endpoint also defines a threshold for the response timing where anything greater than 200 milliseconds will
cause an incident to be raised.

```
{
  "@type": "endpoint-definitions",
  "groups": [
    {
      "@type": "environment-group",
      "id": "customer ABC",
      "logo": "",
      "environments": [
        {
          "@type": "environment",
          "id": "production",
          "endpoint-groups": [
            {
              "@type": "endpoint-group",
              "id": "external",
              "enabled": "true",
              "logo": "",
              "endpoints": [
                {
                  "@type": "endpoint",
                  "id": "website",
                  "url": "https://www.example.com/index.html",
                  "expected": "^[2]\\d\\d$",
                  "threshold": {
                    "min": 0,
                    "max": 200
                  },
                  "retry": 3,
                  "appendTraceID": true,
                  "traceArgumentKey": "cupcake_trace_id",
                  "appendAttempt": true,
                  "attemptArgumentKey": "cupcake_attempt"
                }
              ]
            },
            {
              "@type": "endpoint-group",
              "id": "internal",
              "logo": "",
              "enabled": "true",
              "endpoints": [
                {
                  "@type": "endpoint",
                  "id": "redis",
                  "url": "tcp://redis.internal:6379"
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

## Alert definition file

Alerts are defined in a separate file. Each alert has a type, an ID and whatever properties it needs to operate. Alerts
are grouped together by defining an `alert-group` which references the `id` of the alerts in an array. See the next
section for an example.

There are two standard groups: `default` and `summary`.

The `default` group contains the IDs of alerts that should receive incident notifications in the absence of an
overriding instruction in the endpoints hierarchy. The `summary` group contains the IDs of alerts that should receive
the summary digest that is emitted at startup and periodically thereafter.

### Example

```
{
  "@type": "alert-definitions",
  "groups": [
    {
      "@type": "alert-group",
      "id": "default",
      "alerts": [
        "my-slack-channel",
        "my-aws-list"
      ]
    },
    {
      "@type": "alert-group",
      "id": "summary",
      "alerts": [
        "my-slack-channel"
      ]
    },
    {
      "@type": "alert-group",
      "id": "mysite",
      "alerts": [
        "my-slack-channel"
      ]
    }
  ],
  "alerts": [
    {
      "@type": "alert-slack",
      "id": "my-slack-channel",
      "url": "https://hooks.slack.com/services/xxx/yyy/zzz"
    },
    {
      "@type": "alert-sns",
      "id": "my-aws-list",
      "arn": "xxx",
      "region": "yyy"
    }
  ]
}
```

## Metrics definitions file

Metrics output is also defined in a separate file. Like alerts, different metrics output streams are organised into
groups, with `default` being the default collection of metrics streams that response times will be sent to.

### Example

```
{
  "@type": "metrics-definitions",
  "groups": [
    {
      "@type": "metrics-group",
      "id": "default",
      "metrics": [
        "cloudwatch"
      ]
    }
  ],
  "metrics": [
    {
      "@type": "metrics",
      "id": "cloudwatch",
      "provider": {
        "@type": "cloudwatch",
        "region": "eu-west-1",
        "namespace": "CUPCAKE"
      }
    }
  ]
}
```
