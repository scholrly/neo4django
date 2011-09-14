def setup():
    global Person, neo4django, settings, gdb, models

    from neo4django.tests import Person, neo4django, gdb, models

def teardown():
    gdb.cleandb()

def test_unique():
    """
    Tests unique property behavior.
    """

    class UniqueName(models.NodeModel):
        name = models.StringProperty(indexed=True, unique=True)

    m = UniqueName(name='Matt')
    m.save()

    c = UniqueName(name='Corbin')
    c.save()

    m2 = UniqueName(name='Matt')
    try:
        m2.save()
    except:
        pass
    else:
        raise AssertionError('A saving second node with the same name should'
                             ' raise an error.')

def test_default_parents_index():
    """
    Tests whether indexed nodes, by default, share a parent index.
    """
    class RootIndexedNode(models.NodeModel):
        name = models.StringProperty(indexed=True)

    class ChildIndexedNode(RootIndexedNode):
        name1 = models.StringProperty(indexed=True)

    class GrandChildIndexedNode(ChildIndexedNode):
        name2 = models.StringProperty(indexed=True)

    root = RootIndexedNode(name='dave')
    root.save()

    child = ChildIndexedNode(name1='deandra')
    child.save()

    grandchild = GrandChildIndexedNode(name2='donald')
    grandchild.save()

    assert RootIndexedNode.index()['name']['dave'][0].id == root.pk,\
            "The root node wasn't indexed properly."
    assert RootIndexedNode.index()['name1']['deandra'][0].id == child.pk,\
            "The child node wasn't indexed properly."
    assert RootIndexedNode.index()['name2']['donald'][0].id == grandchild.pk,\
            "The grandchild node wasn't indexed properly."

def test_indexed_types():
    from neo4django.constants import TYPE_ATTR
    from neo4jrestclient.client import NotFoundError

    def get_indexed_type_ids(cls):
        try:
            return [i.id for i in cls.index()[TYPE_ATTR][cls._type_name()]]
        except NotFoundError:
            return []

    class SomeType(models.NodeModel):
        pass

    s = SomeType()
    s.save()

    assert s.pk in get_indexed_type_ids(SomeType), "Initial type was not indexed."

    class SomeOtherType(SomeType):
        pass

    s2 = SomeOtherType()
    s2.save()

    assert s2.pk in get_indexed_type_ids(SomeType), "Subtype not indexed with parent type."
    assert s2.pk in get_indexed_type_ids(SomeOtherType), "Subtype not indexed."

    old_pk = s2.pk
    s2.delete()

    assert old_pk not in get_indexed_type_ids(SomeType), "Subtype not removed from parent index."
    assert old_pk not in get_indexed_type_ids(SomeOtherType), "Subtype not removed from parent index.."
