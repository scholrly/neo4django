__all__ = ['connection', 'connections','DEFAULT_DB_ALIAS']

from django.conf import settings as _settings
from django.core import exceptions
from neo4jrestclient import client as _client
from time import time as _time

from ..utils import ConnectionHandler, StubbornDict
from ..constants import VERSION

# patch the client request for a different User-Agent header
class Neo4djangoRequest(_client.Request):
    _pre_request_callbacks = []
    _post_request_callbacks = []

    @classmethod
    def register_pre_request_callback(cls, callback):
        """
        Register a callback to be called before each request is executed. The
        callback should take a neo4jrestclient.Request as its first argument,
        followed by the request method, url, a data dict, and a headers dict.
        """
        cls._pre_request_callbacks.append(callback)

    @classmethod
    def register_post_request_callback(cls, callback):
        """
        Register a callback to be called after each request is executed. The
        callback should take a neo4jrestclient.Request as its first argument,
        followed by the request method, url, a data dict, and a headers dict.
        """
        cls._post_request_callbacks.append(callback)

    @classmethod
    def unregister_pre_request_callback(cls, callback):
        """
        Unregister a pre-request callback. If the callback has been registered
        multiple times, only the first will be unregistered.
        """
        cls._pre_request_callbacks.remove(callback)

    @classmethod
    def unregister_post_request_callback(cls, callback):
        """
        Unregister a post-request callback. If the callback has been registered
        multiple times, only the first will be unregistered.
        """
        cls._post_request_callbacks.remove(callback)

    def _request(self, method, url, data={}, headers={}):
        headers = headers or {}
        headers['User-Agent'] = 'Neo4django/%s' % VERSION
        # keep the User-Agent key from being reset
        headers = StubbornDict(('User-Agent',), headers)
        #call all pre-request callbacks
        for callback in self._pre_request_callbacks:
            callback(self, method, url, data, headers)
        #create the actual request
        request = super(Neo4djangoRequest, self)._request(method, url, data,
                                                          headers)
        #call all post-request callbacks
        for callback in self._post_request_callbacks:
            callback(self, method, url, data, headers)
        return request

_client.Request = Neo4djangoRequest

if getattr(_settings, 'NEO4DJANGO_PROFILE_REQUESTS', False):
    #add profiling callbacks
    profiling_data = {'last_print_time':_time()}
    def start_timer(req, method, url, data, *args):
        from sys import stdout
        new_time = _time()
        time_diff_between_calls = new_time - profiling_data['last_print_time']
        print "after %0.3f seconds..." % time_diff_between_calls
        profiling_data['last_print_time'] = new_time
        print "{0} {1}".format(method.upper(), url)
        if isinstance(data, (dict, basestring, int)):
            print data
        else:
            print [d.items() for d in data]
        stdout.flush()

    def stop_timer(*args):
        time_diff = _time() - profiling_data['last_print_time']
        print "took %0.3f seconds..." % time_diff

    _client.Request.register_pre_request_callback(start_timer)
    _client.Request.register_post_request_callback(stop_timer)

DEFAULT_DB_ALIAS = 'default'

if not _settings.NEO4J_DATABASES:
    raise exceptions.ImproperlyConfigured('You must configure a Neo4j database '
                                          'to use Neo4j models.')

if not DEFAULT_DB_ALIAS in _settings.NEO4J_DATABASES:
    raise exceptions.ImproperlyConfigured('You must configure a default Neo4j '
                                          'database, \"%s\",to use Neo4j models'
                                          '.' % DEFAULT_DB_ALIAS)

connections = ConnectionHandler(_settings.NEO4J_DATABASES)
#TODO: think about emulating django's db routing

class DefaultConnectionProxy(object):
    """
    Proxy for accessing the default DatabaseWrapper object's attributes. If you
    need to access the DatabaseWrapper object itself, use
    connections[DEFAULT_DB_ALIAS] instead.
    """
    def __getattr__(self, item):
        return getattr(connections[DEFAULT_DB_ALIAS], item)

    def __setattr__(self, name, value):
        return setattr(connections[DEFAULT_DB_ALIAS], name, value)
connection = DefaultConnectionProxy()
