__all__ = ['connection', 'connections','DEFAULT_DB_ALIAS']

from django.conf import settings as _settings
from neo4jrestclient.client import GraphDatabase as _GraphDatabase
from neo4jrestclient import client as _client
from random import random as _random
from time import sleep as _sleep

if getattr(_settings, 'NEO4DJANGO_PROFILE_REQUESTS', False):
    class ProfilingRequest(_client.Request):
        def _request(self, method, url, data={}, headers={}):
            from sys import stdout
            print "{0} {1}".format(method.upper(), url)
            if isinstance(data, (dict, basestring, int)):
                print data
            else:
                print [d.items() for d in data]
            stdout.flush()
            return super(ProfilingRequest, self)._request(method, url,
                                                          data=data,
                                                          headers=headers)
    _client.Request = ProfilingRequest
 
class EnhancedGraphDatabase(_GraphDatabase):
    def gremlin(self, script, **params):
        """
        Execute a Gremlin script server-side and return the results.
        Transactions will be automatically managed, unless otherwise requested
        in the script.
        """
        ext = self.extensions.GremlinPlugin
        return ext.execute_script(script, params=params)

    def gremlin_tx(self, script, **params):
        """
        Execute a Gremlin script server-side and return the results. The script
        will be wrapped in a transaction.

        In addition to standard Gremlin and Neo4j exposed variables,
        `lockManager` provides the script a reference to Neo4j's lock manager.
        """
        tx_script = \
        """
        g.setMaxBufferSize(0)
        g.startTransaction()
        lockManager = g.getRawGraph().getConfig().getLockManager()

        %s
        
        g.stopTransaction(TransactionalGraph.Conclusion.SUCCESS)
        g.setMaxBufferSize(1)

        results
        """ % script
        return self.gremlin(tx_script, **params)

    def gremlin_tx_deadlock_proof(self, script, retries, **params):
        tx_script = \
        """
        import org.neo4j.kernel.DeadlockDetectedException

        g.setMaxBufferSize(0)
        lockManager = g.getRawGraph().getConfig().getLockManager()

        for (deadlockRetry in 1..10) {
            try {
                g.startTransaction()

                %s

                g.stopTransaction(TransactionalGraph.Conclusion.SUCCESS)
                break
            }
            catch(DeadlockDetectedException e) {
                results = "DEADLOCK"
                g.stopTransaction(TransactionalGraph.Conclusion.FAILURE)
            }
        }

        g.setMaxBufferSize(1)

        results
        """ % script
        ret = self.gremlin(tx_script, **params)
        if ret == 'DEADLOCK':
            if retries > 0:
                _sleep(_random()/100.0)
                self.gremlin_tx_deadlock_proof(script, retries - 1, **params)
            else:
                raise RuntimeError('Server-side deadlock detected!')
        return ret

    def cypher(self, query, **params):
        ext = self.extensions.CypherPlugin
        return ext.execute_query(query=query, params=params)

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
