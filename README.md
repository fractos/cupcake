# Cupcake

This is a very simple HTTP, HTTPS and TCP endpoint monitor intended to be the simplest thing that works.

It will work through a file that defines groups of environments and endpoints, currently as a single thread.

If an endpoint times out for connection or if an HTTP/HTTPS endpoint returns a different status code than is expected, then an alert is processed. Cupcake records previous alerts in a backing database and therefore will only alert once for a failure, but will note a return to service for an endpoint together with an approximate length of time that the outage occurred for.

Expected status codes for HTTP/HTTPS services is specified with a regular expression.

At the moment Cupcake is only able to emit alerts via a webhook URL such as the one used by Slack's custom webhook integration. This will shortly be joined by the ability to emit alerts to an SNS topic.

## Environment variables

| Name                       | Description                                                        | Default                        |
|----------------------------|--------------------------------------------------------------------|--------------------------------|
| SLACK_WEBHOOK_URL          | URL of Slack webhook                                               |                                |
| SLEEP_SECONDS              | Number of seconds to yield between runs                            | 60                             |
| ENDPOINT_DEFINITIONS_FILE  | Full path of endpoint definitions file                             | /opt/app/config/endpoints.json |
| ALERT_DEFINITIONS_FILE     | Full path of alert definitions file                                | /opt/app/config/alerts.json    |
| CONNECTION_TIMEOUT_SECONDS | Number of seconds before HTTP(S) and TCP connections will timeout  | 10                             |
| DB_TYPE                    | Type of database to use. Possible values: `sqlite` or `postgresql` | sqlite                         |

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
      "arn": "xxx"
    }
  ]
}
```
