from logzero import logger
import boto3
import requests
import json


def get_alerts_in_group(alert_group_id, alert_definitions):
    logger.debug("get_alert_group: {}".format(alert_group_id))
    for alert_group in alert_definitions["groups"]:
        if alert_group["id"] == alert_group_id:
            for alert_id in alert_group["alerts"]:
                yield alert_id


def deliver_alert_to_groups(incident, alert_groups, alert_definitions):
    logger.debug("deliver_alert_to_groups")
    for alert_group_id in alert_groups:
        deliver_alert_to_group(incident, alert_group_id, alert_definitions)


def deliver_alert_to_group(incident, alert_group_id, alert_definitions):
    logger.debug("deliver_alert_to_group: delivering to group id {}".format(alert_group_id))
    for alert_id in get_alerts_in_group(alert_group_id, alert_definitions):
        deliver_alert(incident, alert_id, alert_definitions)


def deliver_alert(incident, alert_id, alert_definitions):
    logger.debug("deliver_alert: delivering to id {}".format(alert_id))
    for alert in alert_definitions["alerts"]:
        if alert["id"] == alert_id:
            if alert["@type"] == "alert-slack-webhook":
                alert_slack(incident, alert)
            elif alert["@type"] == "alert-sns":
                alert_sns(incident, alert)


def alert_slack(incident, alert):
    logger.debug("alert_slack: {} {}".format(alert["id"], incident.message))
    _ = requests.post(alert["url"], json={"text": incident.message, "link_names": 1})


def alert_sns(incident, alert):
    logger.debug("alert_sns: {} {}".format(alert["id"], incident.message))

    sns_client = boto3.client("sns", alert["region"])

    _ = sns_client.publish(
        TopicArn=alert["arn"],
        Message=json.dumps(incident.as_dict(), indent=4, sort_keys=True)
    )
