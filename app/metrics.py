from logzero import logger
import boto3
import json

from models import Metric, Endpoint

_cloudwatch_client = None

def get_metrics_in_group(metrics_group_id, metrics_definitions):
    logger.debug("get_metrics_in_group: {}".format(metrics_group_id))
    for metrics_group in metrics_definitions["groups"]:
        if metrics_group["id"] == metrics_group_id:
            for metrics_id in metrics_group["metrics"]:
                yield metrics_id


def deliver_metric_to_groups(metric, metrics_groups, metrics_definitions):
    logger.debug("deliver_metric_to_groups ({})".format(",".join(metrics_groups)))
    for metrics_group_id in metrics_groups:
        deliver_metric_to_group(metric, metrics_group_id, metrics_definitions)


def deliver_metric_to_group(metric, metrics_group_id, metrics_definitions):
    logger.debug("deliver_metric_to_group: delivering to group id {}".format(metrics_group_id))
    for metrics_id in get_metrics_in_group(metrics_group_id, metrics_definitions):
        deliver_metric(metric, metrics_id, metrics_definitions)


def deliver_metric(metric, metrics_id, metrics_definitions):
    logger.debug("deliver_metric: delivering to id {}".format(metrics_id))
    for metrics in metrics_definitions["metrics"]:
        if metrics["id"] == metrics_id:
            if metrics["provider"]["@type"] == "cloudwatch":
                metrics_cloudwatch(metric, metrics)


def metrics_cloudwatch(metric, metrics):
    logger.debug("metrics_cloudwatch: {} {}".format(metrics["id"], metric.data))
    cloudwatch = cloudwatch_client(metrics["provider"])
    cloudwatch.put_metric_data(
        MetricData=[
            {
                'MetricName': metric.name,
                'Dimensions': [
                    {
                        'Name': 'ENVIRONMENT-GROUP',
                        'Value': metric.endpoint.environment_group
                    },
                    {
                        'Name': 'ENVIRONMENT',
                        'Value': metric.endpoint.environment
                    },
                    {
                        'Name': 'ENDPOINT-GROUP',
                        'Value': metric.endpoint.endpoint_group
                    },
                    {
                        'Name': 'ENDPOINT',
                        'Value': metric.endpoint.endpoint
                    },
                ],
                'Unit': 'Milliseconds',
                'Value': metric.data
            },
        ],
        Namespace=metrics["provider"]["namespace"]
    )


def cloudwatch_client(provider):
    global _cloudwatch_client
    if _cloudwatch_client is None:
        _cloudwatch_client = boto3.client("cloudwatch", provider["region"])
    return _cloudwatch_client
