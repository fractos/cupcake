import abc

class Database(object, metaclass=abc.ABCMeta):

  @abc.abstractmethod
  def initialise(self, settings):
    raise NotImplementedError("must define initialise() to use this base class")

  @abc.abstractmethod
  def get_active(self, environment_group, environment, endpoint_group, endpoint):
    raise NotImplementedError("must define get_active() to use this base class")

  @abc.abstractmethod
  def get_all_actives(self):
    raise NotImplementedError("must define get_all_actives() to use this base class")

  @abc.abstractmethod
  def active_exists(self, environment_group, environment, endpoint_group, endpoint):
    raise NotImplementedError("must define active_exists() to use this base class")

  @abc.abstractmethod
  def save_active(self, environment_group, environment, endpoint_group, endpoint, timestamp, message):
    raise NotImplementedError("must define save_active() to use this base class")

  @abc.abstractmethod
  def remove_active(self, environment_group, environment, endpoint_group, endpoint):
    raise NotImplementedError("must define remove_active() to use this base class")
