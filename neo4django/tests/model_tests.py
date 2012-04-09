"""
The neo4django test suite. Currently, these are rough development-oriented tests,
and need to be expanded to be more robust.
"""

from nose.tools import eq_

def setup():
    global Person, neo4django, gdb, neo4jrestclient, neo_constants, settings, models

    from neo4django.tests import Person, neo4django, gdb, neo4jrestclient, \
            neo_constants, settings, models

def teardown():
    gdb.cleandb()

def test_custom_manager():

    class MyCustomManager(neo4django.db.models.manager.NodeModelManager):
        def my_custom_manager_method(self):
            pass


    class CustomPerson(Person):
        objects = MyCustomManager()

    assert CustomPerson.objects.model is CustomPerson
    assert hasattr(CustomPerson.objects, 'my_custom_manager_method')

def test_save_delete():
    """Basic sanity check for NodeModel save and delete.  """
    from neo4jrestclient.client import NotFoundError

    pete = Person(name='Pete')
    pete.save()
    node_id = pete.id
    pete.delete()
    try:
        gdb.nodes.get(node_id)
    except NotFoundError:
        pass
    else:
        assert False, 'Pete was not properly deleted.'

def test_type_nodes():
    """Tests for type node existence and uniqueness."""
    class TestType(models.NodeModel):
        class Meta:
            app_label = 'type_node_test'

    n1 = TestType()
    n1.save()

    class TestType(models.NodeModel):
        class Meta:
            app_label = 'type_node_test'

    n2 = TestType()
    n2.save()

    class SecondTestType(TestType):
        class Meta:
            app_label = 'type_node_test2'

    n3 = SecondTestType()
    n3.save()

    class SecondTestType(TestType):
        class Meta:
            app_label = 'type_node_test2'

    n4 = SecondTestType()
    n4.save()

    test_type_nodes = filter(
        lambda n: (n['app_label'], n['model_name']) == ('type_node_test','TestType'),
        gdb.traverse(types=[neo4jrestclient.Outgoing.get('<<TYPE>>')],
                     stop=neo_constants.STOP_AT_END_OF_GRAPH))
    assert len(test_type_nodes) != 0, 'TestType type node does not exist.'
    assert len(test_type_nodes) <= 1, 'There are multiple TestType type nodes.'

    test_type_nodes = filter(
        lambda n: (n['app_label'], n['model_name']) == ('type_node_test2','SecondTestType'),
        gdb.traverse(types=[neo4jrestclient.Outgoing.get('<<TYPE>>')],
                     stop=neo_constants.STOP_AT_END_OF_GRAPH))

    assert len(test_type_nodes) != 0, 'SecondTestType type node does not exist.'
    assert len(test_type_nodes) <= 1, 'There are multiple SecondTestType type nodes.'

def test_model_inheritance():
    #TODO docstring
    class TypeOPerson(Person):
        class Meta:
            app_label = 'newapp'
        hobby = models.Property()

    jake = TypeOPerson(name='Jake', hobby='kayaking')
    jake.save()
    assert jake.hobby == 'kayaking'
   
def test_nodemodel_independence():
    """Tests that NodeModel subclasses can be created and deleted independently."""

    class TestSubclass(models.NodeModel):
        age = models.IntegerProperty()
    
    n1 = TestSubclass(age = 5)
    n1.save()

    class TestSubclass(models.NodeModel):
        pass
    
    n2 = TestSubclass()

    assert not hasattr(n2, 'age'), "Age should not be defined, as the new class didn't define it."

    n2.save()

    assert not hasattr(n2, 'age'),  "Age should not be defined, as the new class didn't define it."

def test_model_casting():
    """Tests functional saved model to model "casting"."""
    #create a model similar to person, but with relationships
    class Doppelganger(models.NodeModel):
        name = models.StringProperty()
        original = models.Relationship(Person,
                                           rel_type=neo4django.Outgoing.MIMICS,
                                           single=True)
    #create a person
    abe = Person.objects.create(name='Abraham Lincoln', age=202)
    #cast it to the new model
    imposter = Doppelganger.from_model(abe)
    imposter.original = abe
    imposter.save()
    #ensure the values are the same
    eq_(abe.name, imposter.name)
    #create another model with only relationships
    class Vierfachganger(models.NodeModel):
        original = models.Relationship(Person,
                                           rel_type=neo4django.Outgoing.MIMICS,
                                           single=True)
    #cast to that model, and see if it works
    double_imposter = Vierfachganger.from_model(imposter)
    eq_(abe, double_imposter.original)

def test_model_casting_validation():
    raise NotImplementedError('Write this test!')

def test_model_copy():
    class NameOwner(models.NodeModel):
        name = models.StringProperty()
        confidantes = models.Relationship(Person, neo4django.Outgoing.KNOWS)

    pete = Person(name='Pete')
    pete2 = NameOwner.copy_model(pete)
    eq_(pete.name, pete2.name)

    pete2.confidantes.add(pete)
    pete3 = NameOwner.copy_model(pete2)
    assert pete in list(pete3.confidantes.all()),\
            "Copying isn't commuting relationships!"


