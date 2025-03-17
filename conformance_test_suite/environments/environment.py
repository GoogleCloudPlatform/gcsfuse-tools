from abc import ABC, abstractmethod

class environment(ABC):
    "Interface for creating environments"
    def __init__(self, config): pass

    """ Set up the environment """
    @abstractmethod
    def setup(): pass
    
    """ Teardown the environment """
    @abstractmethod
    def teardown(): pass

    """ Execute the command """
    @abstractmethod
    def execute(self, task): pass

    """ get mount directory """
    @abstractmethod
    def mount_dir(self, task): pass