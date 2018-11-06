import abc

class Metrics(object, metaclass=abc.ABCMeta):

    @abc.abstractmethod
    def initialise(self, settings):
        raise NotImplementedError("must define initialise() to use this base class")

    @abc.abstractmethod
    def record_response_time(self, metric):
        raise NotImplementedError("must define record_response_time() to use this base class")

    @abc.abstractmethod
    def record_incident(self, incident):
        raise NotImplementedError("must defined record_incident() to use this base class")
