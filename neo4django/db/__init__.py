__all__ = ['connection', 'connections','DEFAULT_DB_ALIAS']

from django.conf import settings as _settings
from django.core import exceptions
from neo4jrestclient import client as _client
from time import time as _time

from ..utils import ConnectionHandler, StubbornDict
from ..constants import VERSION

# patch the client request for a different User-Agent header
class Neo4djangoRequest(_client.Request):
    def _request(self, method, url, data={}, headers={}):
        headers = headers or {}
        headers['User-Agent'] = 'Neo4django/%s' % VERSION
        # keep the User-Agent key from being reset
        headers = StubbornDict(('User-Agent',), headers) 
        return super(Neo4djangoRequest, self)._request(method, url, data,
                                                       headers)
_client.Request = Neo4djangoRequest

if getattr(_settings, 'NEO4DJANGO_PROFILE_REQUESTS', False):
    class ProfilingRequest(_client.Request):
        last_profiling_print = _time()
        def _request(self, method, url, data={}, headers={}):
            from sys import stdout
            new_time = _time()
            print "after %0.3f seconds..." % (new_time - ProfilingRequest.last_profiling_print)
            ProfilingRequest.last_profiling_print = new_time
            print "{0} {1}".format(method.upper(), url)
            if isinstance(data, (dict, basestring, int)):
                print data
            else:
                print [d.items() for d in data]
            stdout.flush()
            ret = super(ProfilingRequest, self)._request(method, url,
                                                          data=data,
                                                          headers=headers)
            print "took %0.3f seconds..." % (_time() - new_time)
            return ret
    _client.Request = ProfilingRequest

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
