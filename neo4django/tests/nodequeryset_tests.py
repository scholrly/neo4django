from nose.tools import with_setup, eq_

from django.core import exceptions

from time import time
import itertools
import sys, datetime
stdout = sys.stdout

def setup():
    global Person, neo4django, gdb, Query, OPERATORS, IndexedMouse, \
           DEFAULT_DB_ALIAS, Condition, models, RelatedCat, RelatedDog 

    from neo4django.tests import Person, neo4django, gdb
    from neo4django.db import DEFAULT_DB_ALIAS, models
    from neo4django.db.models.query import Query, OPERATORS, \
            Condition

    from django.db.models import get_model

    RelatedCat = get_model('tests','RelatedCat')
    RelatedDog = get_model('tests','RelatedDog')
    IndexedMouse = get_model('tests','IndexedMouse')

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
                             "deleted.")

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

def test_none():
    eq_(len(Person.objects.none()), 0)

def make_mice(names, ages):
    for name, age in zip(names, ages):
        IndexedMouse.objects.create(name=name,age=age)

mouse_names = ['jerry','Brain', 'Pinky']
mouse_ages = [2,3,2]
def setup_mice():
    make_mice(mouse_names, mouse_ages)

def make_people(names, ages):
    pairs = zip(names, ages)
    for p in pairs:
        Person.objects.create(name=p[0], age=p[1])

people_names = ['Jack','Jill','Peter Pan','Tinker Bell','Candleja-']

def setup_people():
    make_people(people_names, [5,10,15,15,30])

setup_people.num_people=5

def setup_mice_and_people():
    setup_mice()
    setup_people()

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

    for i in xrange(50):
        Person.objects.create()
    eq_(len(Person.objects.all()), setup_people.num_people + 50)

def test_queryset_str():
    q = Person.objects.all()
    str(q)

@with_setup(setup_mice, teardown)
def test_basic_indexed_query():
    """
    Tests a basic query over a single type. Only indexed fields are tested.
    """
    
    age_query = Query(IndexedMouse).add(IndexedMouse.age, 2)
    results = list(age_query.execute(DEFAULT_DB_ALIAS))
    eq_(len(results), 2)
    assert len([m for m in results if m.name == 'Brain']) == 0, "The query"\
            " returned Brain - even though he's too old."

    results = list(age_query.add(IndexedMouse.name, 'jerry')\
                   .execute(DEFAULT_DB_ALIAS))
    eq_(len(results), 1)
    assert len([m for m in results if m.name == 'jerry']) > 0, "The query"\
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
    assert len([m for m in results if m.name == 'jerry']) == 0, "The query"\
            " returned jerry, even though he was excluded."

@with_setup(setup_people, teardown)
def test_unindexed_query():
    """
    Tests a query over a single type. Only non-indexed fields are tested.
    """
    query = Query(Person).add(Person.name, 'Peter Pan')
    results = list(query.execute(DEFAULT_DB_ALIAS))

    eq_(len(results), 1)
    eq_(results[0].name, 'Peter Pan')

@with_setup(setup_people, teardown)
def test_complex_query():
    """
    Tests a single-type query with both indexed and non-indexed fields.
    """
    query = Query(Person).add(Person.name, 'Peter Pan', negate=True).add(Person.age, 15)
    results = list(query.execute(DEFAULT_DB_ALIAS))

    eq_(len(results), 1)
    eq_(results[0].name, 'Tinker Bell')

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

@with_setup(setup_people, teardown)
def test_get_by_id():
    """
    Tests Queryset.get() using id as a filter parameter.
    """
    name = "The world's most interesting man"
    age = 150
    interesting_man = Person.objects.create(name=name, age=age)
    p1 = Person.objects.get(id=interesting_man.id)
    eq_(p1.name, name)
    eq_(p1.age, age)

    try:
        p2 = Person.objects.get(name="Less interesting man", id=interesting_man.id)
    except exceptions.ObjectDoesNotExist:
        pass
    else:
        raise AssertionError('Interesting man was returned, though has has the '
                             'wrong name.')

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

@with_setup(setup_people, teardown)
def test_in_id():
    """
    Tests Queryset.filter() with an id__in field lookup.
    """
    name = "The world's most interesting man"
    age = 150
    interesting_man = Person.objects.create(name=name, age=age)

    boring_name = 'uninteresting man'
    boring_age = age - 1
    uninteresting_man = Person.objects.create(name=boring_name, age=boring_age)

    Person.objects.create(age=boring_age)
    
    people = list(Person.objects.filter(id__in=(interesting_man.id, uninteresting_man.id)))
    eq_(len(people), 2)
    eq_([boring_age, age], sorted(p.age for p in people))

    people = list(Person.objects.filter(age=boring_age)
                  .filter(id__in=(interesting_man.id, uninteresting_man.id)))
    eq_(len(people), 1)
    eq_(people[0].id, uninteresting_man.id)

    single_person = list(Person.objects.filter(id__in=(interesting_man.id,)))
    eq_(len(single_person), 1)

    no_people = list(Person.objects.filter(id__in=(1000,)))
    eq_(len(no_people), 0)

    # Test chaining
    only_interesting = Person.objects.filter(
        id__in=(interesting_man.id,)
        ).filter(id__in=(interesting_man.id, uninteresting_man.id))
    eq_(len(only_interesting), 1)

    only_interesting = Person.objects.filter(
        id__in=(interesting_man.id,)
        ).filter(id__in=(uninteresting_man.id,))
    eq_(len(only_interesting), 0)
    
    # Passing in an empty qs -- replicate django
    eq_(len(Person.objects.filter(id__in=[])), 0)

    # Passing in qs with None -- replicate django
    eq_(len(Person.objects.filter(id__in=[uninteresting_man.id, None])), 1)

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
    # now check an unindexed property
    all_named_after_t = Person.objects.filter(name__gt='T')
    eq_(len(all_named_after_t), 3)


@with_setup(setup_teens, teardown)
def test_filter_gte():
    teens_and_up = Person.objects.filter(age__gte=12)
    assert all(12 <= p.age for p in teens_and_up), 'Not all teenage or older!'
    assert len(teens_and_up) > 0, 'No one returned!'
    assert any(p.name == 'Tiny Tim' for p in teens_and_up),\
            "Tiny Tim was excluded! That sucks, he's 12!"
    # now check an unindexed property
    all_named_after_tiny_tim = Person.objects.filter(name__gte='Tiny Tim')
    eq_(len(all_named_after_tiny_tim), 1)

@with_setup(setup_teens, teardown)
def test_filter_lt():
    kids_only = Person.objects.filter(age__lt=13)
    assert all(p.age < 13 for p in kids_only), 'Not all under 13!'
    assert len(kids_only) > 0, 'No one returned!'
    assert any(p.name == 'Tiny Tim' for p in kids_only),\
            "Tiny Tim was excluded! That sucks, he's 12!"
    # now check an unindexed property
    all_named_before_d = Person.objects.filter(name__lt='D')
    eq_(len(all_named_before_d), 1)

@with_setup(setup_teens, teardown)
def test_filter_lte():
    kids_only = Person.objects.filter(age__lte=12)
    assert all(p.age <= 12 for p in kids_only), 'Not all under 12!'
    assert len(kids_only) > 0, 'No one returned!'
    assert any(p.name == 'Tiny Tim' for p in kids_only),\
            "Tiny Tim was excluded! That sucks, he's 12!"
    # now check an unindexed property
    all_named_before_candle = Person.objects.filter(name__lte='Candleja-')
    eq_(len(all_named_before_candle), 1)

alphabet = [chr(i + 97) for i in range(26)]
def test_filter_range():
    import random
    ages_and_names = zip(*[(''.join(random.sample(alphabet, 6)), i + 70) for i in xrange(20)])
    make_people(*ages_and_names)
    octogenarians = Person.objects.filter(age__range=(80, 89))
    assert all(80 <= p.age <= 89 for p in octogenarians), "These guys aren't all in their 80's!"

    # now check an unindexed property
    make_people(['Tom'], [25])
    all_between_s_u = Person.objects.filter(name__range=('S','U'))
    assert len(all_between_s_u) >= 1, "There's at least one 'T' name!"

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

@with_setup(None, teardown)
def test_filter_array_member():
    """
    Tests the new `field__member` array membership field lookup.
    """
    class TooManyAccounts(Person):
        names = models.StringArrayProperty()
        emails = models.StringArrayProperty(indexed=True)

    names = ['Test1','Rob','Bill']
    emails = ['test1@example.com','test2@example.com','test3@example.com']
    p1 = TooManyAccounts.objects.create(names=names[:2], emails=emails[:2])
    p2 = TooManyAccounts.objects.create(names=names[1:], emails=emails[1:])

    q_1only = TooManyAccounts.objects.filter(emails__member=emails[0])
    q_both = TooManyAccounts.objects.filter(emails__member=emails[1])

    eq_(len(q_1only), 1)
    eq_(list(q_1only)[0].id, p1.id)

    eq_(set(p.id for p in q_both), set((p1.id, p2.id)))

    # now check an unindexed property
    q_both = TooManyAccounts.objects.filter(names__member=names[1])
    eq_(len(q_both), 2)
    eq_(set(p.id for p in q_both), set((p1.id, p2.id)))

    q_1only = TooManyAccounts.objects.filter(names__member=names[0])
    eq_(len(q_1only), 1)
    eq_(list(q_1only)[0].id, p1.id)

@with_setup(setup_teens, teardown)
def test_filter_in():
    q = Person.objects.filter(age__in=[15, 12])
    
    eq_(len(q), 4)
    assert all(p.age in [15,12] for p in q)

    # now check an unindexed property
    tim_and_rob = Person.objects.filter(name__in=['Tiny Tim','Rob'])
    eq_(len(tim_and_rob), 2)

@with_setup(None, teardown)
def test_filter_array_member_in():
    """
    Tests the `field__member_in` array membership field lookup.
    """
    class TooManyAccounts(Person):
        names = models.StringArrayProperty()
        emails = models.StringArrayProperty(indexed=True)

    names = ['Test1','Rob','Bill']
    emails = ['test1@example.com','test2@example.com','test3@example.com']
    p1 = TooManyAccounts.objects.create(names=names[:2], emails=emails[:2])
    p2 = TooManyAccounts.objects.create(names=names[1:], emails=emails[1:])

    q_1only = TooManyAccounts.objects.filter(emails__member_in=[emails[0]])
    q_both = TooManyAccounts.objects.filter(emails__member_in=emails)

    eq_(len(q_1only), 1)
    eq_(list(q_1only)[0].id, p1.id)

    eq_(set(p.id for p in q_both), set((p1.id, p2.id)))

    # now check an unindexed property
    q_both = TooManyAccounts.objects.filter(names__member_in=names)
    eq_(len(q_both), 2)
    eq_(set(p.id for p in q_both), set((p1.id, p2.id)))

    q_1only = TooManyAccounts.objects.filter(names__member_in=[names[0]])
    eq_(len(q_1only), 1)
    eq_(list(q_1only)[0].id, p1.id)

#test isnull

@with_setup(None, teardown)
def test_exclude_exact():
    pass

#TODO other test excludes

@with_setup(setup_people, teardown)
def test_in_bulk():
    """
    Tests Queryset.in_bulk().
    """
    name = "The world's most interesting man"
    age = 150
    interesting_man = Person.objects.create(name=name, age=age)

    boring_name = 'uninteresting man'
    boring_age = age - 1
    uninteresting_man = Person.objects.create(name=boring_name, age=boring_age)

    Person.objects.create(age=boring_age)

    people = Person.objects.in_bulk((interesting_man.id, uninteresting_man.id))
    eq_(len(people), 2)
    eq_(people[interesting_man.id].name, name)
    eq_([boring_age, age], sorted(p.age for p in people.values()))

@with_setup(setup_people, teardown)
def test_in_bulk_not_found():
    """
    Tests QuerySet.in_bulk() with items not found.
    """
    people = Person.objects.in_bulk([999999])
    eq_(people, {})

@with_setup(setup_mice_and_people, teardown)
def test_contains():
    q1 = Person.objects.filter(name__contains='a')

    eq_(len(q1), 3)
    assert all('a' in p.name for p in q1)

    q2 = IndexedMouse.objects.filter(name__contains='y')
    eq_(len(q2), 2)

@with_setup(setup_mice_and_people, teardown)
def test_startswith():
    q1 = Person.objects.filter(name__startswith='J')

    eq_(len(q1), 2)
    assert all(p.name.startswith('J') for p in q1)

    q2 = IndexedMouse.objects.filter(name__startswith='P')
    eq_(len(q2), 1)

cat_names = ['Tom', 'Mr. Pussy-Wussy', 'Mr. Bigglesworth']
dog_names = ['Spike','Lassie','Clifford']
def setup_chase():
    cats = [RelatedCat.objects.create(name=n) for n in cat_names]
    dogs = [RelatedDog.objects.create(name=n) for n in dog_names]
    mice = [IndexedMouse.objects.create(name=n) for n in mouse_names]

    for m, c, d in zip(mice, cats, dogs):
        c.chases = m
        d.chases = c
        c.save()
        d.save()

@with_setup(setup_chase, teardown)
def test_select_related():
    def check_dog_hier_from_q(queryset):
        dogs = []
        cats = []
        mice = []
        for d in queryset:
            dogs.append(d)
            for c in d.chases.all():
                cats.append(c)
                for m in c.chases.all():
                    mice.append(m)
                    m.name
        
        #check correctness, leave performance for benchmarking
        spike = filter(lambda d: d.name == 'Spike', dogs)[0]
        tom = filter(lambda c: c.name == 'Tom', cats)[0]
        jerry = filter(lambda m: m.name == 'jerry', mice)[0]
        eq_(list(spike.chases.all())[0], tom)
        eq_(list(tom.chases.all())[0], jerry)
    
    check_dog_hier_from_q(RelatedDog.objects.all().select_related(depth=2))

    #test reverse relation with an index-based query
    jerry = IndexedMouse.objects.all().select_related().get(name='jerry')
    jerry_chasers = list(jerry.relatedcat_set.all())
    eq_(len(jerry_chasers), 1)
    eq_(jerry_chasers[0].name, 'Tom')
    
    #try the hierarchy with a field-based select_related
    check_dog_hier_from_q(RelatedDog.objects.all().select_related('chases','chases__chases'))

@with_setup(setup_chase, teardown)
def test_spanning_lookup():
    #test the regular relation
    tom = RelatedCat.objects.get(chases__name='Jerry')
    eq_(tom.name, 'Tom')

    spike = RelatedDog.objects.get(chases__chases__name='Jerry')
    eq_(spike.name, 'Spike')

    #then test the reverse
    tom = RelatedCat.objects.all().get(relateddog_set__name='Spike')
    eq_(tom.name, 'Tom')

    jerry = IndexedMouse.objects.all().get(relatedcat_set__relateddog_set__name='Spike')
    eq_(jerry.name, 'Jerry')

@with_setup(None, teardown)
def test_large_query():
    ages = range(1, 151)
    names = ['a mouse'] * len(ages)
    make_mice(names, ages)

    mice =  list(IndexedMouse.objects.filter(age__in=ages))
    eq_(len(mice), len(ages))

@with_setup(None, teardown)
def test_zerovalued_lookup():
    ages = range(2)
    make_mice(['a','a'], ages)

    mice =  list(IndexedMouse.objects.filter(age__in=ages))
    eq_(len(mice), len(ages))

@with_setup(None, teardown)
def test_get_or_create():
    name = "Kristian"
    (obj1, created) = Person.objects.get_or_create(name=name)
    assert created == True, "Person should have been created but wasn't"
    (obj2, created) = Person.objects.get_or_create(name=name)
    assert created == False, "Person should not have been created, one should already exist"
    assert obj1 == obj2, "Second get_or_create() should have returned Person created by first get_or_create()"

@with_setup(None, teardown)
def test_object_index():
    class PollIdx(models.NodeModel):
            question = models.StringProperty()
            def __unicode__(self):
                return self.question
    p1 = PollIdx(question="Who's the best")
    p2 = PollIdx(question="How's the weather?")
    p3 = PollIdx(question="What's up?")
    p1.save()
    p2.save()
    p3.save()

    eq_(len(PollIdx.objects.all()), 3)
    p0 = PollIdx.objects.all()[0]
    p1 = PollIdx.objects.all()[1]
    p2 = PollIdx.objects.all()[2]
    eq_(len(set([p0, p1, p2, p0])), 3, "There should be 3 different polls")

    qsall = PollIdx.objects.all()
    # Should fill up cache one by one
    eq_(qsall._result_cache, None)
    p0 = qsall[0]
    eq_(len(qsall._result_cache), 1)
    p1 = qsall[1]
    eq_(len(qsall._result_cache), 2)
    p2 = qsall[2]
    eq_(len(qsall._result_cache), 3)
    eq_(len(set([p0, p1, p2, p0, p1])), 3, "There should be 3 different polls")

    qsall = PollIdx.objects.all()
    # Filling up the cache first
    len(qsall)
    eq_(len(qsall._result_cache), 3)
    p0 = qsall[0]
    p1 = qsall[1]
    p2 = qsall[2]
    eq_(len(set([p0, p1, p2, p0, p1, p2])), 3, "There should still be 3 different polls")

@with_setup(setup_people, teardown)
def test_order_by():
    people = Person.objects.all().order_by('age')
    eq_(len(people), 5)
    eq_(list(people), sorted(list(people), key=lambda p:p.age))
    
    # check the reverse order
    people = Person.objects.all().order_by('-age')
    eq_(list(people), sorted(list(people), key=lambda p:p.age, reverse=True))

@with_setup(setup_people, teardown)
def test_exists():
    eq_(Person.objects.all().exists(), True)
    eq_(Person.objects.all().filter(name='Candleja-').exists(), True)
    eq_(Person.objects.all().filter(age__gt=80).exists(), False)
    eq_(Person.objects.all().filter(age__lt=80).exists(), True)

@with_setup(setup_people, teardown)
def test_count():
    eq_(Person.objects.all().count(), 5)
    eq_(Person.objects.all().filter(name='Candleja-').count(), 1)
    eq_(Person.objects.all().filter(age__gt=10).count(), 3)

@with_setup(setup_people, teardown)
def test_aggregate_count():
    from django.db.models import Count

    eq_(Person.objects.all().aggregate(Count('age')).get('age__count', None), 5)
    eq_(Person.objects.all().filter(name='Candleja-').aggregate(Count('age')).get('age__count', None), 1)
    eq_(Person.objects.filter(age__gt=10).aggregate(Count('name')).get('name__count', None), 3)

@with_setup(setup_people, teardown)
def test_aggregate_max_min():
    from django.db.models import Max, Min

    eq_(Person.objects.all().aggregate(Min('age')).get('age__min', None), 5)
    eq_(Person.objects.all().aggregate(Max('age')).get('age__max', None), 30)
    eq_(Person.objects.all().filter(name='Candleja-').aggregate(Min('age')).get('age__min', None), 30)

@with_setup(setup_people, teardown)
def test_aggregate_sum():
    from django.db.models import Sum

    eq_(Person.objects.all().aggregate(Sum('age')).get('age__sum', None), 75)
    eq_(Person.objects.all().filter(name='Candleja-').aggregate(Sum('age')).get('age__sum', None), 30)

@with_setup(setup_people, teardown)
def test_aggregate_avg():
    from django.db.models import Avg

    eq_(Person.objects.all().aggregate(Avg('age')).get('age__avg', None), 15)
    eq_(Person.objects.all().filter(name='Candleja-').aggregate(Avg('age')).get('age__avg', None), 30)

def test_query_type():
    """
    Confirms #151 - ensures sibling types are not returned on query, even if the
    the related index is at the parent level.
    """
    class IndexedParent(models.NodeModel):
        name = models.StringProperty(indexed=True)

    class Sibling1(IndexedParent):
        pass

    class Sibling2(IndexedParent):
        pass

    s1 = Sibling1.objects.create(name='Amanda')
    s2 = Sibling2.objects.create(name='Other Amanda')

    eq_(len(Sibling1.objects.filter(name__contains='Amanda')), 1)
    eq_(len(Sibling2.objects.filter(name__contains='Amanda')), 1)

    eq_(len(IndexedParent.objects.filter(name__contains='Amanda')), 2)
