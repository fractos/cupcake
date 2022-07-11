import json

class Incident:
    """
    Hold the fields associated with an incident
    """

    def __init__(self, timestamp, endpoint=None, result={}, expected="", message="", presentation_message=""):
        self.timestamp = timestamp
        self.endpoint = endpoint
        self.result = result
        self.expected = expected
        self.message = message
        self.presentation_message = presentation_message

    def as_dict(self):
        return {
            "timestamp": self.timestamp,
            "environment_group": "" if self.endpoint is None else self.endpoint.environment_group,
            "environment": "" if self.endpoint is None else self.endpoint.environment,
            "endpoint_group": "" if self.endpoint is None else self.endpoint.endpoint_group,
            "endpoint": "" if self.endpoint is None else self.endpoint.endpoint,
            "result": self.result,
            "url": "" if self.endpoint is None else self.endpoint.url,
            "expected": self.expected,
            "message": self.message,
            "presentation_message": self.presentation_message
        }


class Threshold:
    """
    Analyse a timing against a threshold
    """

    def __init__(self, threshold):
        if "min" in threshold:
            self.min = threshold["min"]
        else:
            self.min = None
        if "max" in threshold:
            self.max = threshold["max"]
        else:
            self.max = None

    def result(self, test_time):
        milliseconds = int(round(int(getattr(test_time, "microsecond")) / 1000.0))

        if self.min is not None and milliseconds < self.min:
            return ThresholdResult(
                okay=False,
                result="time {}ms less than minimum {}ms".format(milliseconds, self.min)
            )

        if self.max is not None and milliseconds > self.max:
            return ThresholdResult(
                okay=False,
                result="time {}ms greater than maximum {}ms".format(milliseconds, self.max)
            )

        return ThresholdResult()


class ThresholdResult:
    """
    Hold a result from a threshold analysis
    """

    def __init__(self, okay=True, result=""):
        self.okay = okay
        self.result = result


class Endpoint:
    """
    Represent an endpoint's metadata
    """

    def __init__(self, environment_group, environment, endpoint_group, endpoint, url, retry, timeout):
        self.environment_group = environment_group
        self.environment = environment
        self.endpoint_group = endpoint_group
        self.endpoint = endpoint
        self.url = url
        self.retry = retry
        self.timeout = timeout

    def __repr__(self):
        return "{} {} {} {} {}".format(
            self.environment_group,
            self.environment,
            self.endpoint_group,
            self.endpoint,
            self.url
        )


class Metric:
    """
    Represent a metric to be recorded
    """

    def __init__(self, endpoint, timestamp, name, data):
        self.endpoint = endpoint
        self.timestamp = timestamp
        self.name = name
        self.data = data
