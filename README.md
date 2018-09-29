# Cupcake

[![Build Status](https://travis-ci.org/fractos/cupcake.svg?branch=master)](https://travis-ci.org/fractos/cupcake)

This is a very simple HTTP, HTTPS and TCP endpoint monitor intended to be the simplest thing that works.

It will work through a file that defines groups of environments and endpoints, currently as a single thread.

If an endpoint times out for connection or if an HTTP/HTTPS endpoint returns a different status code than is expected, then an alert is processed. Cupcake records previous alerts in a backing database and therefore will only alert once for a failure, but will note a return to service for an endpoint together with an approximate length of time that the outage occurred for.

Expected status codes for HTTP/HTTPS services is specified with a regular expression.

At the moment Cupcake is only able to emit alerts via a webhook URL such as the one used by Slack's custom webhook integration. This will shortly be joined by the ability to emit alerts to an SNS topic.

## Environment variables

| Name                       | Description                                                              | Default                        |
|----------------------------|--------------------------------------------------------------------------|--------------------------------|
| SLEEP_SECONDS              | Number of seconds to yield between runs                                  | 60                             |
| ENDPOINT_DEFINITIONS_FILE  | Full path of endpoint definitions file                                   | /opt/app/config/endpoints.json |
| ALERT_DEFINITIONS_FILE     | Full path of alert definitions file                                      | /opt/app/config/alerts.json    |
| CONNECTION_TIMEOUT_SECONDS | Number of seconds before HTTP(S) and TCP connections will timeout        | 10                             |
| DB_TYPE                    | Type of database to use. Possible values: `sqlite` or `postgresql`       | sqlite                         |
| EMIT_SUMMARY               | Whether to emit a summary / digest message to a subset of alert channels | True                           |
| SUMMARY_SLEEP_SECONDS      | Number of seconds between emitting summary digests                       | 86400                          |

Note:

It is important to have CONNECTION_TIMEOUT_SECONDS set to a value less than 30, as when a process in a containerised environment such as ECS is redeployed or stopped then it will be given a SIGTERM signal and a 30 second timeout before a SIGKILL signal is sent that will kill the process immediately. Cupcake tests whether it has been requested to stop after each endpoint measurement and intercepts SIGTERM and SIGINT in order to try and quit as soon as possible after receiving them.

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

The following defines an environment group called "customer ABC" which has an environment called "production". Within that environment are two endpoint groups - "external" and "internal". The "external" endpoint group contains an HTTPS URL for the main website including a regular expression that defines the HTTP status code that it expects to receive (any status code in range 2xx). The "internal" endpoint group contains a TCP URL for a Redis server. It is assumed for this example that Cupcake is situated on a server that is inside the private network and therefore is able to lookup a host named "redis.internal" using some kind of internal DNS scheme (e.g. Route53).

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
                  "expected": "^[2]\\d\\d$"
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

Alerts are defined in the following way:

```
{
  "@type": "alert-definitions",
  "alerts": [
    {
      "@type": "alert-slack",
      "url": "https://hooks.slack.com/services/xxx/yyy/zzz"
    },
    {
      "@type": "alert-sns",
      "arn": "xxx",
      "region": "yyy"
    }
  ]
}
```
