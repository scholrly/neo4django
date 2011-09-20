__all__ = ['connection', 'connections','DEFAULT_DB_ALIAS']

from django.conf import settings as _settings
from neo4jrestclient.client import GraphDatabase as _GraphDatabase

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
    connections[key] = _GraphDatabase('http://%s:%d/db/data' %
                                     (value['HOST'], value['PORT']))

#TODO: think about emulating django's db routing
connection = connections[DEFAULT_DB_ALIAS]
