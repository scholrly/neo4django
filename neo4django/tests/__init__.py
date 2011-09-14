import requests

def setup():
    global neo4django, neo4jrestclient, gdb, Person, settings, neo_constants
    global models

    from django.conf import settings

    import neo4django, neo4jrestclient.client as neo4jrestclient
    from neo4django.db import models
    import neo4jrestclient.constants as neo_constants
    gdb_set = settings.NEO4J_DATABASES['default']
    gdb = neo4jrestclient.GraphDatabase('http://%s:%s%s' % 
                        (gdb_set['HOST'], str(gdb_set['PORT']), gdb_set['ENDPOINT']))

    class Person(models.NodeModel):
        name = models.Property()
        age = models.IntegerProperty(indexed=True)

    key = getattr(settings, 'NEO4J_DELETE_KEY', None)
    server = getattr(settings, 'NEO4J_DATABASES', None)
    server = server.get('default', None) if server else None

    def cleandb():
        resp = requests.delete('http://%s:%s/cleandb/%s' %
                               (server['HOST'], str(server['PORT']), key))
        if resp.status_code != 200:
            print "\nTest database couldn't be cleared - have you installed the cleandb extension at https://github.com/jexp/neo4j-clean-remote-db-addon?"

    if None not in (key, server):
        gdb.cleandb = cleandb
    else:
        gdb.cleandb = lambda : None
