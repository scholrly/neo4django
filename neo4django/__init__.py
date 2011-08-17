import neo4jrestclient.client as neo4j

from django.conf import settings
from django.core import exceptions

__all__ = ['connections','DEFAULT_DB_ALIAS', 'Outgoing', 'Incoming', 'All',
           'NodeModel', 'Relationship', 'Property', 'StringProperty', 'EmailProperty',
           'URLProperty', 'IntegerProperty', 'DateProperty', 'DateTimeProperty',
           'StringArrayProperty', 'IntArrayProperty']

class GraphDatabase(neo4j.GraphDatabase):
    def __init__(self, *args, **kwargs):
        return super(GraphDatabase, self).__init__(*args, **kwargs)


DEFAULT_DB_ALIAS = 'default'

if not settings.NEO4J_DATABASES:
    raise exceptions.ImproperlyConfigured('You must configure a Neo4j database '
                                          'to use Neo4j models.')

if not DEFAULT_DB_ALIAS in settings.NEO4J_DATABASES:
    raise exceptions.ImproperlyConfigured('You must configure a default Neo4j '
                                          'database, \"%s\",to use Neo4j models'
                                          '.' % DEFAULT_DB_ALIAS)

connections = {}

#maybe move this to a ConnectionHandler ala django.db
for key, value in settings.NEO4J_DATABASES.items():
    if 'HOST' not in value or 'PORT' not in value:
        raise exceptions.ImproperlyConfigured('Each Neo4j database configured '
                                              'needs a configured host and '
                                              'port.')
    connections[key] = GraphDatabase('http://%s:%d/db/data' %
                                     (value['HOST'], value['PORT']))

connection = connections[DEFAULT_DB_ALIAS]

#TODO: think about emulating django's db routing

from neo4jrestclient.client import Incoming, Outgoing, All

from models import NodeModel
from relationships import Relationship
from properties import Property, StringProperty, EmailProperty, URLProperty,\
        IntegerProperty, DateProperty, DateTimeProperty, ArrayProperty, \
        StringArrayProperty, IntArrayProperty, URLArrayProperty
