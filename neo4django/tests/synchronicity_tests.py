from nose.tools import eq_

from threading import Thread
from Queue import Queue
from time import sleep

def setup():
    global Person, neo4django, gdb, neo4jrestclient, neo_constants, settings, models

    from neo4django.tests import Person, neo4django, gdb, neo4jrestclient, \
            neo_constants, settings, models

#def teardown():
#    gdb.cleandb()

def test_typenode_transactionality():
    class RaceModel(models.NodeModel):
        pass

    exc_queue = Queue()

    def race():
        r = RaceModel()
        try:
            r.save()
        except Exception, e:
            exc_queue.put(str(e))
        else:
            exc_queue.put(True)

    num_threads = 3
    for i in xrange(num_threads):
        thread = Thread(target=race)
        thread.start()

    for i in xrange(num_threads):
        val = exc_queue.get()
        if val is not True:
            raise AssertionError('There was an error saving one of the '
                                     'RaceModels (#%d) - "%s"' % (i, val))
    #check the number of typenodes
    typenode_script = "g.v(0).outE('<<TYPE>>').inV.filter{it.model_name=='%s'}"
    typenode_script %= RaceModel.__name__
    typenodes = gdb.extensions.GremlinPlugin.execute_script(typenode_script)
    eq_(len(typenodes), 1)
