from nose.tools import eq_, with_setup

from threading import Thread
from Queue import Queue
from time import sleep

def setup():
    global Person, neo4django, gdb, neo4jrestclient, neo_constants, settings, models

    from neo4django.tests import Person, neo4django, gdb, neo4jrestclient, \
            neo_constants, settings
    from neo4django.db import models

def teardown():
    gdb.cleandb()

@with_setup(None, teardown)
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

    num_threads = 5
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

def race(func, num_threads):
    """
    Run a multi-threaded race on func. Func should accept a single argument-
    a Queue. If func succeeds, it should `q.put(True)`- if it fails, it should
    `q.put('error message')`.
    """

    exc_queue = Queue()

    for i in xrange(num_threads):
        thread = Thread(target=func, args=(exc_queue,))
        thread.start()

    for i in xrange(num_threads):
        val = exc_queue.get()
        if val is not True:
            raise AssertionError('There was an error running race (#%d) - "%s"'
                                 % (i, val))

@with_setup(None, teardown)
def test_autoproperty_transactionality():
    class AutoRaceModel(models.NodeModel):
        some_id = models.AutoProperty()

    def autorace(queue):
        r = AutoRaceModel()
        try:
            r.save()
        except Exception, e:
            queue.put(str(e))
        else:
            queue.put(True)
    
    race(autorace, 3)
    eq_(len(set(m.some_id for m in AutoRaceModel.objects.all())), 3)
