from nose.tools import with_setup, eq_

import sys, datetime
stdout = sys.stdout

def setup():
    global Person, neo4django, gdb, Query, OPERATORS, IndexedMouse, \
            DEFAULT_DB_ALIAS, return_filter_from_conditions, Condition, models

    from neo4django.tests import Person, neo4django, gdb, models
    from neo4django.db import DEFAULT_DB_ALIAS
    from neo4django.db.models.query import Query, OPERATORS, \
            return_filter_from_conditions, Condition

    class IndexedMouse(models.NodeModel):
        name = models.StringProperty(indexed=True)
        age = models.IntegerProperty(indexed=True)

def teardown():
    gdb.cleandb()

@with_setup(None, teardown)
def test_create():
    """Confirm 'create()' works for NodeQuerySet."""
    pete = Person.objects.create(name='Pete')
    try:
        gdb.nodes.get(pete.pk)
    except:
        raise AssertionError('Pete was not created or was not given a primary '
                             'key.')
 
@with_setup(None, teardown)
def test_delete():
    """Confirm 'delete()' works for NodeQuerySet."""
    jack = Person(name='jack')
    jack.save()
    jacks_pk = jack.pk
    Person.objects.filter(name='jack').delete()
    try:
        gdb.nodes.get(jacks_pk)
    except:
        pass
    else:
        raise AssertionError("Jack's pk is still in the graph- he wasn't "
                             "created.")

@with_setup(None, teardown)
def test_iter():
    """Confirm 'all()' is iterable."""
    for p in Person.objects.all():
        pass

@with_setup(None, teardown)
def test_dates():
    """Testing dates() with simple time right now""" 
    
    class DatedPaper(models.NodeModel):
        name = models.StringProperty()
        date = models.DateProperty()
        datetime = models.DateTimeProperty()
        
    day0 = datetime.date.today()
    time0 = datetime.datetime.now()
    paper = DatedPaper(name='Papes', date = day0, datetime = time0)
    paper.save()
    day1 = datetime.date.today()
    time1 = datetime.datetime.now()
    other = DatedPaper(name='other', date = day1, datetime = time1)
    other.save()
    results = DatedPaper.objects.dates('day', 'year').iterator()
    paper = results.next()
    other = results.next()
    assert paper.name == 'Papes'
    assert other.name == 'other'
    assert paper.datetime < other.datetime
    
def setup_mice():
    IndexedMouse.objects.create(name='jerry',age=2)
    IndexedMouse.objects.create(name='Brain',age=3)
    IndexedMouse.objects.create(name='Pinky',age=2)

def make_people(names, ages):
    pairs = zip(names, ages)
    for p in pairs:
        Person.objects.create(name=p[0], age=p[1])

people_names = ['Jack','Jill','Peter Pan','Tinker Bell','Candleja-']

def setup_people():
    make_people(people_names, [5,10,15,15,30])

setup_people.num_people=5

@with_setup(setup_people, teardown)
def test_all():
    """
    Tests that all() returns all saved models of a type, and that calling it
    twice returns two distinct Querysets.
    """
    results = list(Person.objects.all())
    eq_(len(results), setup_people.num_people)

    names = set(p.name for p in results)
    for name in people_names:
        assert name in names, '%s is not in %s' % (name, repr(name))

    clone1 = Person.objects.all()
    clone2 = Person.objects.all()
    assert clone1 is not clone2

@with_setup(setup_mice, teardown)
def test_basic_indexed_query():
    """
    Tests a basic query over a single type. Only indexed fields are tested.
    """
    
    age_query = Query(IndexedMouse).add(IndexedMouse.age, 2)
    results = list(age_query.execute(DEFAULT_DB_ALIAS))
    eq_(len(results), 2)
    assert len([m for m in results if m['name'] == 'Brain']) == 0, "The query"\
            " returned Brain - even though he's too old."

    results = list(age_query.add(IndexedMouse.name, 'jerry')\
                   .execute(DEFAULT_DB_ALIAS))
    eq_(len(results), 1)
    assert len([m for m in results if m['name'] == 'jerry']) > 0, "The query"\
            " didn't return jerry - wrong mouse."

@with_setup(setup_mice, teardown)
def test_negated_query():
    """
    Tests a negated query over a single type. Only indexed fields are tested.
    """
    query = Query(IndexedMouse).add(IndexedMouse.age, 2)\
            .add(IndexedMouse.name, 'jerry', negate=True)
    results = list(query.execute(DEFAULT_DB_ALIAS))
    eq_(len(results), 1)
    assert len([m for m in results if m['name'] == 'jerry']) == 0, "The query"\
            " returned jerry, even though he was excluded."

@with_setup(setup_people, teardown)
def test_unindexed_query():
    """
    Tests a query over a single type. Only non-indexed fields are tested.
    """
    query = Query(Person).add(Person.name, 'Peter Pan')
    results = list(query.execute(DEFAULT_DB_ALIAS))

    eq_(len(results), 1)
    eq_(results[0]['name'], 'Peter Pan')

@with_setup(setup_people, teardown)
def test_complex_query():
    """
    Tests a single-type query with both indexed and non-indexed fields.
    """
    query = Query(Person).add(Person.name, 'Peter Pan', negate=True).add(Person.age, 15)
    results = list(query.execute(DEFAULT_DB_ALIAS))

    eq_(len(results), 1)
    eq_(results[0]['name'], 'Tinker Bell')

@with_setup(None, teardown)
def test_type_query():
    """
    Tests that Query properly excludes results of different types.
    """
    #TODO
    raise NotImplementedError('Write this test!')

@with_setup(setup_people, teardown)
def test_get():
    """
    Tests Queryset.get() with and without filter parameters.
    """
    name = "The world's most interesting man"
    age = 150
    Person.objects.create(name=name, age=age)
    p = Person.objects.all().get(name=name, age=age)
    eq_(p.name, name)
    eq_(p.age, age)

@with_setup(None, teardown)
def test_filter_exact():
    #TODO docstring
    make_people(['tom', 'jerry', 'jErry'], [1,2,2])
    try:
        tom = Person.objects.filter(age=1).get()
        assert tom.name == 'tom', "Returned Person from filtered queryset "\
                                  "doesn't have the correct name."
    except ValueError:
        assert False, 'More than one object exists in the queryset - it was '
        'improperly filtered.'
    #test multiple conditions
    try:
        jerry = Person.objects.filter(age=2).filter(name='jErry').get()
        assert jerry.name == 'jErry', "Returned Person from filtered queryset "\
                                  "doesn't have the correct name."
    except ValueError:
        assert False, 'More than one object exists in the multi-condition '\
                      'queryset - it was improperly filtered.'

@with_setup(None, teardown)
def test_filter_iexact():
    make_people(['tom', 'jerry', 'jErry'], [1,2,2])
    jerrys = Person.objects.filter(name__iexact='jerry')
    eq_(len(list(jerrys)), 2)

#test in

def setup_teens():
    setup_people()
    make_people(['Tina', 'Rob', 'Tiny Tim'], [13, 15, 12])

setup_teens.num_people = setup_people.num_people + 3

@with_setup(setup_teens, teardown)
def test_filter_gt():
    teens_and_up = Person.objects.filter(age__gt=13)
    assert all(p.age > 13 for p in teens_and_up), 'Not all teenage or older!'
    assert len(teens_and_up) > 0, 'No one returned!'
    assert not any(p.name == 'Tiny Tim' for p in teens_and_up),\
            "Tiny Tim was included, but he's too young!"

@with_setup(setup_teens, teardown)
def test_filter_gte():
    teens_and_up = Person.objects.filter(age__gte=12)
    assert all(12 <= p.age for p in teens_and_up), 'Not all teenage or older!'
    assert len(teens_and_up) > 0, 'No one returned!'
    assert any(p.name == 'Tiny Tim' for p in teens_and_up),\
            "Tiny Tim was excluded! That sucks, he's 12!"

@with_setup(setup_teens, teardown)
def test_filter_lt():
    kids_only = Person.objects.filter(age__lt=13)
    assert all(p.age < 13 for p in kids_only), 'Not all under 13!'
    assert len(kids_only) > 0, 'No one returned!'
    assert any(p.name == 'Tiny Tim' for p in kids_only),\
            "Tiny Tim was excluded! That sucks, he's 12!"

@with_setup(setup_teens, teardown)
def test_filter_lte():
    kids_only = Person.objects.filter(age__lte=12)
    assert all(p.age <= 12 for p in kids_only), 'Not all under 12!'
    assert len(kids_only) > 0, 'No one returned!'
    assert any(p.name == 'Tiny Tim' for p in kids_only),\
            "Tiny Tim was excluded! That sucks, he's 12!"

alphabet = [chr(i + 97) for i in range(26)]
def test_filter_range():
    import random
    ages_and_names = zip(*[(random.sample(alphabet, 6), i + 70) for i in xrange(20)])
    make_people(*ages_and_names)
    octogenarians = Person.objects.filter(age__range=(80, 89))
    assert all(80 <= p.age <= 89 for p in octogenarians), "These guys aren't all in their 80's!"

@with_setup(None, teardown)
def test_filter_date_range():
    class Lifetime(models.NodeModel):
        dob = models.DateProperty(indexed=True)
        mid_life_crisis = models.DateTimeProperty(indexed=True)
        tod = models.DateTimeProperty(indexed=False)
    date = datetime.date
    time = datetime.datetime
    bdays = [date(1952, 3, 5), date(1975, 8, 11), date(1988, 7, 27)]
    crises = [time(1992, 3, 6, 2, 15, 30), time(2007, 8, 13, 16, 10, 10),
              time(2020, 8, 1, 8, 7, 59, 99)]
    tods = [time(2022, 3, 6, 2, 15, 30), time(2047, 10, 30, 22, 47, 1),
              time(2060, 8, 15, 8, 7, 59)]
    for t in zip(bdays, crises, tods):
        Lifetime.objects.create(dob=t[0], mid_life_crisis=t[1], tod=t[2])

    low, high = date(1975, 9, 11), time.now()
    query = Lifetime.objects.filter(dob__range=(low, high))
    assert all(low < l.dob < high.date() for l in query)

    nowish = date(2011, 8, 10)
    query = Lifetime.objects.filter(mid_life_crisis__lt=nowish)
    eq_(len(query), 2)

    the_singularity = date(2032, 12, 12)
    query = Lifetime.objects.filter(tod__gt=the_singularity)
    eq_(len(query), 2)

#test isnull

@with_setup(None, teardown)
def test_exclude_exact():
    pass

#other test excludes
