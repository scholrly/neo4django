from django import test as django_test
from django.conf import settings as _settings

from neo4django.db import connections
from utils import ConnectionHandler


class NodeModelTestCase(django_test.TestCase):
    """Cleans  up the graph database in between each run"""
    def __init__(self, *args, **kwargs):
        ## TODO: This really belongs in ``setup_environment`` on a custom
        ##       runner, but we don't want to ask our users to use a custom
        ##       TestCase and a custom runner?
        super(NodeModelTestCase, self).__init__(*args, **kwargs)
        _settings.NEO4J_DATABASES = _settings.NEO4J_TEST_DATABASES
        _settings.RUNNING_NEO4J_TESTS = True
        new_connections = ConnectionHandler(_settings.NEO4J_TEST_DATABASES)
        for alias in new_connections:
            connections[alias] = new_connections[alias]

    def _pre_setup(self, *args, **kwargs):
        for alias in connections:
            connections[alias].cleandb()
        return super(NodeModelTestCase, self)._pre_setup(*args, **kwargs)

    def _post_teardown(self, *args, **kwargs):
        for alias in connections:
            connections[alias].cleandb()
        return super(NodeModelTestCase, self)._post_teardown(*args, **kwargs)
