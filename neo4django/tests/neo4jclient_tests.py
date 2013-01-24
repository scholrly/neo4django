from nose.tools import with_setup, eq_
import random, string
from django.conf import settings as _settings
from ..db import connections
from ..neo4jclient import EnhancedGraphDatabase

class MyGraphDatabase(EnhancedGraphDatabase):
    def do_something(self):
        return "did something"

def setup():
    global neo4django, gdb, neo4jclient, connection

    from neo4django.tests import neo4django, gdb
    from neo4django import neo4jclient
    from neo4django.db import connection

def teardown():
    gdb.cleandb()

def test_cleandb():
    def assert_deletion(ids):
        eq_(connection.gremlin('results=nodeIds.collect{(boolean)g.v(it)}.any()', nodeIds = ids, raw=True), 'false')

    node_id = connection.gremlin('results=g.addVertex().id')
    connection.cleandb()
    assert_deletion([node_id])

    #try it with more nodes
    node_ids = connection.gremlin('results=(0..50).collect{g.addVertex().id}', raw=True)
    connection.cleandb()
    assert_deletion(node_ids)

    #try it with related nodes
    linked_script = '''results = []
                       lastNode=g.v(0);(0..50).each{
                           thisNode=g.addVertex()
                           results << thisNode.id
                           g.addEdge(lastNode, thisNode, "knows",[:]);
                           lastNode = thisNode
                       }'''
    node_ids = connection.gremlin(linked_script, raw=True)
    connection.cleandb()
    assert_deletion(node_ids)

def test_other_library():
    random_lib = """
    class %(class_name)s {
        static public binding;
        static getRoot() {
            return binding.g.v(0)
        }
    }
    %(class_name)s.binding = binding;
    """
    class_name = ''.join(random.choice(string.letters) for i in xrange(6))
    random_lib %= {'class_name':class_name}
    neo4jclient.load_library(class_name, random_lib)

    node = connection.gremlin('results = %s.getRoot()' % class_name)
    eq_(node.id, 0)

def test_custom_clients_same_database():
    """Testing to make sure our custom """
    assert connections['custom'].do_something() == "did something"

    try:
        connections['default'].do_something()
    except AttributeError:
        pass
    else:
        raise AssertionError('Default database should not have access to do_something()')
