import requests

def setup():
    print 'Debugging 1'
    global neo4django, neo4jrestclient, gdb, Person, settings, neo_constants
    print 'Debugging 1'
    global models

    print 'Debugging 1'
    from django.conf import settings
    print 'Debugging 2'
    import neo4django, neo4jrestclient.client as neo4jrestclient
    print 'Debugging 3'
    from neo4django.db import models
    print 'Debugging 4'
    import neo4jrestclient.constants as neo_constants
    print 'Debugging 5'
    gdb_set = settings.NEO4J_DATABASES['default']
    print 'Debugging 6'
    # import pdb; pdb.set_trace()
    gdb = neo4jrestclient.GraphDatabase('http://%s:%s%s' % 
                        (gdb_set['HOST'], str(gdb_set['PORT']), gdb_set['ENDPOINT']))

    print 'Debugging 7'
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
