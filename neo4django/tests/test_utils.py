from mock import Mock, patch
from nose.tools import with_setup, raises
from pretend import stub

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model as DjangoModel

from neo4django import utils
from neo4django.neo4jclient import EnhancedGraphDatabase
from neo4django.db.models import NodeModel


# Nose does a weird copying from unittest asserts, meaning
# assertListEqual didn't move from unittest2 to unittest until
# Python 2.7. The yuck below is for 2.6 compatibility
try:
    from nose.tools import assert_list_equal
except ImportError:
    from itertools import starmap, izip
    from operator import eq as equals

    def assert_list_equal(a, b):
        """
        A simple (but hack) for compatibility with `nose.tools` on python
        2.6. This just asserts that both lists have the same length and that
        the values at the same indexes are equal
        """
        assert len(a) == len(b)
        assert all(starmap(equals, izip(a, b)))


def test_subborn_dict_restricts_keys():
    stubborn = utils.StubbornDict(('foo',), {'bar': 'baz'})

    # Setting a stubborn key will not do anything
    stubborn['foo'] = 'qux'
    assert 'foo' not in stubborn


def test_subborn_dict_allows_keys():
    stubborn = utils.StubbornDict(('foo',), {'bar': 'baz'})

    # We should be able to set a non-stubborn key
    stubborn['qux'] = 'foo'
    assert 'qux' in stubborn


def test_uniqify():
    values = [1, 1, 'foo', 2, 'foo', 'bar', 'baz']
    expected = [1, 'foo', 2, 'bar', 'baz']

    unique_values = utils.uniqify(values)

    assert_list_equal(expected, unique_values)


def test_all_your_base():
    # Establish base classes
    class A(object):
        pass

    class B(A):
        pass

    class C(B):
        pass

    class D(object):
        pass

    class E(C, D):
        pass

    c_bases = [cls for cls in utils.all_your_base(C, A)]
    e_bases = [cls for cls in utils.all_your_base(E, B)]

    assert_list_equal(c_bases, [C, B, A])
    assert_list_equal(e_bases, [E, C, B])


def test_write_through():
    obj = Mock()
    obj._meta.write_through = 'foo'

    assert utils.write_through(obj) == 'foo'


def test_write_through_default():
    obj = object()

    assert utils.write_through(obj) is False


def setup_attrrouter():
    global router, member
    router = utils.AttrRouter()
    member = stub(foo='bar')
    router.member = member


@with_setup(setup_attrrouter, None)
def test_attrrouter_router_default():
    router = utils.AttrRouter()
    assert router._router == {}


@with_setup(setup_attrrouter, None)
def test_attrrouter_with_routed_attrs():
    router.__dict__[router._key] = 'foo'
    assert router._router == 'foo'


@with_setup(setup_attrrouter, None)
def test_attrrouter_gets_obj_attr():
    router.foo = 'bar'
    assert getattr(router, 'foo') == 'bar'


@with_setup(setup_attrrouter, None)
def test_attrrouter_gets_routed():
    # Manually map the routing to ensure we test only what we intend to
    router._router['get'] = {'foo': member}
    assert router.foo == 'bar'


@with_setup(setup_attrrouter, None)
def test_attrrouter_sets_obj_attr():
    router.foo = 'bar'
    assert router.foo == 'bar'


@with_setup(setup_attrrouter, None)
def test_attrrouter_sets_routed():
    # Manually map the routing to ensure we test only what we intend to
    router._router['set'] = {'foo': member}
    router._router['get'] = {'foo': member}

    # Change the value
    router.foo = 'baz'

    # It should update both places
    assert router.foo == 'baz'
    assert member.foo == 'baz'


@with_setup(setup_attrrouter, None)
def test_attrrouter_dels_obj_attr():
    router.foo = 'bar'

    del router.foo

    assert not hasattr(router, 'foo')


@with_setup(setup_attrrouter, None)
def test_attrrouter_dels_routed():
    # Manually map the routing to ensure we test only what we intend to
    router._router['del'] = {'foo': member}
    router._router['get'] = {'foo': member}

    del router.foo

    # It should delete both places
    assert not hasattr(router, 'foo')
    assert not hasattr(member, 'foo')


@with_setup(setup_attrrouter, None)
def test_attrrouter_route_get():
    router._route(('foo',), member, get=True)
    assert router.foo == 'bar'


@with_setup(setup_attrrouter, None)
def test_attrrouter_route_set():
    router._route(('foo',), member, set=True)
    router.foo = 'baz'

    assert router.foo == 'baz'
    assert member.foo == 'baz'


@with_setup(setup_attrrouter, None)
def test_attrrouter_route_delete():
    router._route(('foo',), member, delete=True)
    del router.foo

    # It should delete both places
    assert not hasattr(router, 'foo')
    assert not hasattr(member, 'foo')


@raises(AttributeError)
@with_setup(setup_attrrouter, None)
def test_attrrouter_unroute_get():
    router._route(('foo',), member, get=True)
    router._unroute(('foo',), get=True)
    router.foo


@with_setup(setup_attrrouter, None)
def test_attrrouter_unroute_set():
    # Check routed
    router._route(('foo',), member, set=True)
    router._unroute(('foo',), set=True)
    router.foo = 'baz'

    # Should be different
    assert router.foo == 'baz'
    assert member.foo == 'bar'


@raises(AttributeError)
@with_setup(setup_attrrouter, None)
def test_attrrouter_unroute_delete():
    # Check routed
    router._route(('foo',), member, delete=True)
    router._unroute(('foo',), delete=True)
    del router.foo


class MyDjangoModel(DjangoModel):
    """
    A simple/empty subclass of django.db.models.Model for testing
    """


def test_integration_router_is_node_model():
    router = utils.Neo4djangoIntegrationRouter()
    model = NodeModel()

    assert router._is_node_model(NodeModel)
    assert router._is_node_model(model)


def test_integration_router_allow_relation_mismatch():
    router = utils.Neo4djangoIntegrationRouter()
    node_model = NodeModel()
    django_model = MyDjangoModel()

    assert router.allow_relation(node_model, django_model) is False


def test_integration_router_allow_relation_between_node_models():
    router = utils.Neo4djangoIntegrationRouter()
    node_model1 = NodeModel()
    node_model2 = NodeModel()

    assert router.allow_relation(node_model1, node_model2) is None


def test_integration_router_allow_relation_between_django_models():
    router = utils.Neo4djangoIntegrationRouter()
    django_model1 = MyDjangoModel()
    django_model2 = MyDjangoModel()

    assert router.allow_relation(django_model1, django_model2) is None


@raises(ImproperlyConfigured)
@patch('neo4django.utils.import_module')
def test_load_client_fail_module_import(import_module):
    import_module.side_effect = ImportError

    try:
        utils.load_client('foo.bar.baz')
    except ImproperlyConfigured as e:
        assert 'Could not import' in e.message
        raise e


@raises(ImproperlyConfigured)
@patch('neo4django.utils.import_module')
def test_load_client_module_class_missing(import_module):
    import_module.return_value = stub(foo='bar')

    try:
        utils.load_client('foo.bar.baz')
    except ImproperlyConfigured as e:
        assert 'Neo4j client module' in e.message
        raise e


@raises(ImproperlyConfigured)
@patch('neo4django.utils.import_module')
def test_load_client_not_correct_subclass(import_module):
    MyClass = type('MyClass', (object,), {})
    import_module.return_value = stub(baz=MyClass)

    try:
        utils.load_client('foo.bar.baz')
    except ImproperlyConfigured as e:
        assert 'is not a subclass of EnhancedGraphDatabase' in e.message
        raise e


@patch('neo4django.utils.import_module')
def test_load_client(import_module):
    MyClass = type('MyClass', (EnhancedGraphDatabase,), {})
    import_module.return_value = stub(baz=MyClass)

    assert utils.load_client('foo.bar.baz') == MyClass


def test_sliding_pair():
    ret = utils.sliding_pair(('foo', 'bar', 'baz'))
    expected = [('foo', 'bar'),
                ('bar', 'baz'),
                ('baz', None)]

    assert_list_equal(expected, list(ret))


def test_assignable_list():
    list_obj = utils.AssignableList()
    list_obj.foo = 'bar'

    # Check that we can get the attribute
    assert list_obj.foo == 'bar'

    # We should have added object to _new_attrs
    assert 'foo' in list_obj.get_new_attrs()

    # hasattr() should return True
    assert hasattr(list_obj, 'foo')


def test_enum_numerical_items():
    e = utils.Enum('foo', 'bar', 'baz')
    assert e.FOO == 0
    assert e.BAR == 1
    assert e.BAZ == 2


def test_enum_explict_items():
    e = utils.Enum(foo='bar', baz='qux')
    assert e.FOO == 'bar'
    assert e.BAZ == 'qux'


def test_countdown():
    fn = utils.countdown(5)

    # Should return True 5 times, then false
    assert all([fn() for x in xrange(5)])
    assert not any([fn() for x in xrange(100)])  # Excessive, but proves it


def test_apply_to_buffer():
    fn = Mock(return_value='a')
    ret = utils.apply_to_buffer(fn, xrange(100), size=5)

    assert fn.call_count == 5
    assert_list_equal(ret, ['a', 'a', 'a', 'a', 'a'])


@raises(StopIteration)
def test_apply_to_buffer_raises_stop_iteration():
    fn = Mock(return_value='a')
    utils.apply_to_buffer(fn, xrange(100), size=0)


def test_buffer_iterator():
    expected = [0, 1, 4, 9, 16]
    ret = [x for x in utils.buffer_iterator(lambda x: x**2, xrange(5), size=2)]
    assert_list_equal(ret, expected)
