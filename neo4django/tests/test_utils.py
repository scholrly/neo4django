from mock import Mock
from nose.tools import assert_list_equal

from neo4django import utils


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
