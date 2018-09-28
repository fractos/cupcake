class Incident:
    """
    Hold the fields associated with an incident
    """

    def __init__(self, timestamp, environment_group, environment, endpoint_group, endpoint, result, url, expected, message='', presentation_message=''):
        self.timestamp = timestamp
        self.environment_group = environment_group
        self.environment = environment
        self.endpoint_group = endpoint_group
        self.endpoint = endpoint
        self.result = result
        self.url = url
        self.expected = expected
        self.message = message
        self.presentation_message = presentation_message
