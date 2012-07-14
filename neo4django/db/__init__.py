__all__ = ['connection', 'connections','DEFAULT_DB_ALIAS']

from django.conf import settings as _settings
from django.core import exceptions
from neo4jrestclient import client as _client
from time import time as _time

from ..library_loader import EnhancedGraphDatabase

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

connections = {}

#maybe move this to a ConnectionHandler ala django.db
for key, value in _settings.NEO4J_DATABASES.items():
    if 'HOST' not in value or 'PORT' not in value:
        raise exceptions.ImproperlyConfigured('Each Neo4j database configured '
                                              'needs a configured host and '
                                              'port.')
    connections[key] = EnhancedGraphDatabase('http://%s:%d/db/data' %
                                             (value['HOST'], value['PORT']))

#TODO: think about emulating django's db routing
connection = connections[DEFAULT_DB_ALIAS]
