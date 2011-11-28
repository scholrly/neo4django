import copy
from nose.tools import with_setup

def setup():
    global Person, gdb, models

    from neo4django.tests import Person, gdb, models

def teardown():
    gdb.cleandb()

def test_json_serialize():
    from django.core import serializers
    dave = Person(name='dave')
    dave.save()
    json_serializer = serializers.get_serializer('json')()
    assert json_serializer.serialize(Person.objects.all())
    dave = Person(name='dave', age=12)
    dave.save()
    assert json_serializer.serialize(Person.objects.all())
    dave = Person()
    dave.save()
    assert json_serializer.serialize(Person.objects.all())

@with_setup(None, teardown)
def test_rel_deepcopy():
    """
    Test that `Relationship` instances can be copied (used elsewhere in Django).
    """
    class ZenNode(models.NodeModel):
        rel = models.Relationship('self',rel_type='knows')

    try:
        [copy.deepcopy(f) for f in ZenNode._meta.fields]
    except Exception, e:
        raise AssertionError('Error deepcopying property.', e)

