from nose.tools import eq_, with_setup

import datetime
import itertools

def setup():
    global Person, neo4django, gdb, neo4jrestclient, neo_constants, settings, models

    from neo4django.tests import Person, neo4django, gdb, neo4jrestclient, \
            neo_constants, settings, models

def teardown():
    gdb.cleandb()

def test_prop():
    pete = Person(name='Pete')
    assert pete.name == 'Pete'
    pete.save()
    assert pete.name == 'Pete'
    pete.name = 'Peter'
    assert pete.name == 'Peter'
    pete.save()
    assert pete.name == 'Peter'

def test_none_prop():
    """Confirm that `None` and null verification work properly."""
    pete = Person()
    pete.save()
    assert pete.name is None
    
    class NotNullPerson(models.NodeModel):
        class Meta:
            app_label = 'test'
        name = models.StringProperty(null=False)
    try:
        andy = NotNullPerson(name = None)
        andy.save()
    except:
        pass
    else:
        raise AssertionError('Non-nullable field accepted `None` as a value.')

def test_integer():
    def try_int(integer):
        node = Person(name="SandraInt", age=integer)
        node.save()
        assert node.age == integer
        node.delete()

    for i in [0,1,-1,28,neo4django.db.models.properties.MAX_INT,neo4django.db.models.properties.MIN_INT]:
        try_int(i)
    
def test_date_constructor():
    #TODO
    pass

def test_date_prop():
    #TODO
    pass

def test_datetime_constructor():
    """Confirm `DateTimeProperty`s work from a NodeModel constructor."""
    #TODO cover each part of a datetime
    class DateTimeNode(models.NodeModel):
        datetime = models.DateTimeProperty()

    time = datetime.datetime.now()
    d = DateTimeNode(datetime = time)
    assert d.datetime == time
    d.save()
    assert d.datetime == time

def test_datetime_auto_now():
    from time import sleep
    class BlogNode(models.NodeModel):
        title = models.Property()
        date_modified = models.DateTimeProperty(auto_now = True)
    timediff = .6 #can be this far apart
    ##Confirm the date auto sets on creation
    b = BlogNode(title = 'Snake House')
    b.save()
    time1 = datetime.datetime.now()
    test1, test2 = get_times(time1, b.date_modified)
    assert abs(test1-test2) <= timediff
    ##Confirm the date auto sets when saved and something changes
    sleep(timediff)
    b.title = 'BEEEEEEEES!'
    b.save()
    time2 = datetime.datetime.now()
    test1, test2 = get_times(time2, b.date_modified)
    assert abs(test1-test2) <= timediff
    ##Confirm the date auto sets when saved and nothing changes
    sleep(timediff)
    b.save()
    time3 = datetime.datetime.now()
    test1, test2 = get_times(time3, b.date_modified)
    assert abs(test1-test2) <= timediff

def get_times(t1, t2):
    rv = [t1.second*1.0 + (t1.microsecond/10.0**6), t2.second*1.0 + (t2.microsecond/10.0**6)]
    if t1.minute - t2.minute == 1:
        rv[0] += 60
    elif t2.minute - t1.minute == 1:
        rv[1] += 60
    return rv

def test_datetime_auto_now_add():
    class BloggNode(models.NodeModel):
        title = models.Property()
        date_created = models.DateTimeProperty(auto_now_add = True)
    timediff = .6
    ##Confrim date auto sets upon creation
    time1 = datetime.datetime.now()
    b = BloggNode(title = 'Angry birds attack buildings!')
    b.save()
    test1, test2 = get_times(time1, b.date_created)
    assert abs(test1-test2) <= .6
    time = b.date_created
    ##Confrim the date doesn't change when saved and something changes
    b.title = 'Ape uprising! NYC destroyed!'
    b.save()
    assert b.date_created == time
    ##Confirm the date doesn't change when saved and nothing changes
    b.save()
    assert b.date_created == time

def test_date_auto_now():
    class BlagNode(models.NodeModel):
        title = models.Property()
        date_changed = models.DateProperty(auto_now = True)
    ##Confirm the date auto sets on creation
    b = BlagNode(title = 'Snookie House')
    b.save()
    date1 = datetime.date.today()
    assert b.date_changed == date1
    ##Confirm the date auto sets when saved and something changes
    b.title = 'BEEAAAARRSSSS!'
    b.save()
    date2 = datetime.date.today()
    assert b.date_changed == date2
    ##Confirm the date auto sets when saved and nothing changes
    b.save()
    date3 = datetime.date.today()
    assert b.date_changed == date3

def test_date_auto_now_add():
    class BlegNode(models.NodeModel):
        title = models.Property()
        date_made = models.DateProperty(auto_now_add = True)
    ##Confirm the date auto sets on creation
    b = BlegNode(title = "d")
    b.save()
    date1 = datetime.date.today()
    assert b.date_made == date1
    ##Confirm the date doesn't change when another property changes
    b.title = 'Whoreticulture'
    b.save()
    assert b.date_made == date1
    ##Confrim the date doesn't change when no other property changes
    b.save()
    assert b.date_made == date1

def test_array_property_validator():
    """Tests that ArrayProperty validates properly."""
    class ArrayNode(models.NodeModel):
        vals = models.ArrayProperty()

    n1 = ArrayNode(vals = (1, 2, 3))
    n1.save()
    n2 = ArrayNode(vals = [1, 2, 3])
    n2.save()
    try:
        n3 = ArrayNode(vals = {'1':1, '2':2, '3':3})
        n3.save()
    except:
        pass
    else:
        raise AssertionError('dicts should not work')
    try:
        n4 = ArrayNode(vals = 'hurrr')
        n4.save()
    except:
        pass
    else:
        raise AssertionError('strings should not work')

def test_empty_array():
    """Tests that an empty array is saved and retrieved properly."""
    class EmptyArrayNode(models.NodeModel):
        vals = models.ArrayProperty()

    n1 = EmptyArrayNode()
    n1.vals = []
    n1.save()

    eq_(n1.vals, [])

def test_int_array_property_validator():
    """Tests that IntArrayProperty validates properly."""
    class StrArrayNode(models.NodeModel):
        vals = models.IntArrayProperty()

    n1 = StrArrayNode(vals = (1,2,3))
    n1.save()
    try:
        n2 = StrArrayNode(vals = ('1','2','3'))
        n2.save()
    except:
        pass
    else:
        raise AssertionError('tuples of strs should not work')

def test_str_array_property_validator():
    """Tests that StringArrayProperty validates properly."""
    class StrArrayNode(models.NodeModel):
        vals = models.StringArrayProperty()

    try:
        n2 = StrArrayNode(vals = (1,2,3,))
        n2.save()
    except:
        pass
    else:
        raise AssertionError('tuples of ints should not work')

def test_url_array_property_validator():
    """Tests that StringArrayProperty validates properly."""
    class URLArrayNode(models.NodeModel):
        vals = models.URLArrayProperty()

    n1 = URLArrayNode(vals = ('http://google.com',
                              'https://afsgdfvdfgdf.eu/123/asd',
                              'file://onetwothree.org/qwerty/123456'))
    n1.save()
    try:
        n2 = URLArrayNode(vals = (1,2,3,))
        n2.save()
    except:
        pass
    else:
        raise AssertionError('tuples of ints should not work')

def test_prop_metadata():
    class NodeWithMetadata(models.NodeModel):
        name = models.StringProperty(metadata={'test':123})
    meta_fields = filter(lambda f: hasattr(f, 'meta'), NodeWithMetadata._meta.fields)
    eq_(len(meta_fields), 1)
    assert 'test' in meta_fields[0].meta
    eq_(meta_fields[0].meta['test'], 123)

@with_setup(None, teardown)
def test_auto_property():
    class AutoNode(models.NodeModel):
        some_id = models.AutoProperty()
    nodes = [AutoNode.objects.create() for i in xrange(5)]
    eq_([n.some_id for n in nodes], range(1, 6))

    #test with an abstract parent
    class AbstractAutoNode(models.NodeModel):
        class Meta:
            abstract = True
        some_id = models.AutoProperty()

    class ConcreteAutoNode(AbstractAutoNode):
        pass

    nodes = [ConcreteAutoNode.objects.create() for i in xrange(5)]
    eq_([n.some_id for n in nodes], range(1, 6))

