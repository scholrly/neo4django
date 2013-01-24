from urlparse import urlparse

from django import test as django_test
from django.conf import settings as _settings

from .db import Neo4djangoRequest, connections, DEFAULT_DB_ALIAS
from .utils import ConnectionHandler

class NumRequestsProfiler(object):
    def __init__(self, connection, assertion):
        self.connection = connection
        self.assertion = assertion
        self.num = 0

    def __enter__(self):
        def counter(req, method, url, *args):
            if urlparse(url).netloc == urlparse(self.connection.url).netloc:
                # TODO this is not the best way to test which connection is
                # being counted- it would be nice if requests knew their
                # parent connections
                self.num += 1
        self._callback = counter
        Neo4djangoRequest.register_pre_request_callback(self._callback)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        Neo4djangoRequest.unregister_pre_request_callback(self._callback)
        if exc_type is not None:
            return
        if self.assertion is not None:
            self.assertion(self.num)

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

    def assertNumRequests(self, num, func=None, *args, **kwargs):
        """
        Similar to Django's built-in `assertNumQueries`, but for Neo4j REST
        client requests.
        """
        using = kwargs.pop("using", DEFAULT_DB_ALIAS)
        conn = connections[using]

        def assert_num_requests(actual_num):
            self.assertEqual(num, actual_num)

        context = NumRequestsProfiler(conn, assert_num_requests)
        if func is None:
            return context

        with context:
            func(*args, **kwargs)
